"""PM view: work packages, expanded tender rounds, and real phase blanks."""
def apply_pm_view(app,project,tasks,objects):
    app.FilterClear()
    for task in tasks:
        objects[task['id']].Number20=0 if task.get('pm_view_detail') else 1
    app.OutlineShowTasks(65535)
    app.OptionsViewEx(*([None]*11+[False]))
    app.SetRowHeight(1,'ALL',False)
    app.SelectAll();app.Font32Ex(None,None,False)
    for task in tasks:
        if task.get('outline_level',3)<=2:
            app.SelectRow(int(objects[task['id']].ID),False)
            app.Font32Ex(None,None,True)
    # A task-field filter excludes Project null rows on reopen. Use the
    # report hierarchy with no active filter, processing lower rows first.
    from core.master_reporting import SUPPLY_TRACK
    packages=[t for t in tasks if t.get('summary') and
              (t.get('pm_view_package') or t.get('name')==SUPPLY_TRACK)]
    for task in sorted(packages,key=lambda t:int(objects[t['id']].ID),reverse=True):
        app.SelectRow(int(objects[task['id']].ID),False)
        app.OutlineHideSubTasks()
    visible=[t for t in tasks if not t.get('pm_view_detail')]
    boundaries=[i for i,t in enumerate(visible,1) if t.get('outline_level')==2][1:]
    for row in reversed(boundaries):
        app.SelectRow(row,False)
        app.InsertBlankRow()
