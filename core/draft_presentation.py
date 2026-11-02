"""One presentation contract for an unverified local approval route."""
PREFIX = '【诊断草案·报审路径待核实】'
NOTICE = '报审适用条件或属地规则尚未核实；参考时限用于计划推演，不能视为审批或开工、入驻放行。'

def draft_notice(decision):
    return NOTICE if decision and decision.get('status') in {'NEEDS_LOCAL_RULE','NEEDS_FACTS','NEEDS_CLARIFICATION'} else ''
