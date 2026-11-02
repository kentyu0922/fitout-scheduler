"""Compare daily native working minutes against the existing solver calendar policy.

This checks project configuration equivalence, not legal holiday accuracy.
DateDifference is documented at learn.microsoft.com/en-us/office/vba/api/project.application.datedifference.
"""
from datetime import date, datetime, timedelta
from core.solver_engine import _is_working_day
from core.holidays import build_holiday_bitmap
from core._common import CONSTRUCTION_CAL_NAME, FACTORY_CAL_NAME


def calendar_expectations(start, finish, holidays, site_mode):
    if site_mode not in ('5D','6D','7D'):
        raise ValueError('Unsupported site mode')
    all_days=build_holiday_bitmap([(h['start'],h['finish']) for h in holidays])
    spring=build_holiday_bitmap([(h['start'],h['finish']) for h in holidays if '春节' in h.get('name','')])
    day=date.fromisoformat(start); end=date.fromisoformat(finish)
    if end < day: raise ValueError('Invalid audit range')
    result={key:{} for key in ('Project','Construction','Factory')}
    while day <= end:
        for key,weekend,mode in [('Project',False,'5D'),('Construction',True,site_mode),('Factory',True,'6D')]:
            result[key][day.isoformat()]=480 if _is_working_day(day,False,weekend,all_days,spring,mode) else 0
        day+=timedelta(days=1)
    return result


def compare_calendar_minutes(expected, actual):
    differences=[]
    for name,days in expected.items():
        for day,minutes in days.items():
            observed=actual.get(name,{}).get(day)
            if observed != minutes:
                differences.append(dict(calendar=name,date=day,expected_minutes=minutes,actual_minutes=observed))
    return dict(status='PASS' if expected and all(expected.values()) and not differences else 'FAIL',
                checked_calendar_days=sum(map(len,expected.values())),differences=differences,
                difference_count=len(differences),scope='Every date in the project span; solver working minutes vs native calendar',
                legal_holiday_verification='NOT_IN_SCOPE')


def audit_native_calendars(app, project, start, finish, holidays, site_mode='7D'):
    from exporters.mpp_renderer import _com_time
    expected=calendar_expectations(start,finish,holidays,site_mode)
    calendars={'Project':project.Calendar,'Construction':project.BaseCalendars(CONSTRUCTION_CAL_NAME),
               'Factory':project.BaseCalendars(FACTORY_CAL_NAME)}
    actual={}
    for key,cal in calendars.items():
        print(f'Calendar readback: {key}, {len(expected[key])} days',flush=True)
        actual[key]={}
        for day in expected[key]:
            start_day=datetime.combine(date.fromisoformat(day),datetime.min.time())
            actual[key][day]=float(app.DateDifference(_com_time(start_day),_com_time(start_day+timedelta(days=1)),cal))
    return dict(compare_calendar_minutes(expected,actual),native_minutes=actual)
