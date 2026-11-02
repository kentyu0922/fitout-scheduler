"""Reserved client columns. No keyword-based responsibility assignments.

Scope ownership (owner_module, scope_allocation) remains separate and untouched.
PMs may fill the exported columns in Project; generated plans leave them blank.
"""
FIELD_MAP = {"responsible_unit":"Text1", "responsible_person":"Text2", "responsibility_flag":"Text3"}
FIELD_TITLES = {"Text1":"执行单位", "Text2":"责任人", "Text3":"责任标识"}
def resolve_responsibility(task):
    return dict.fromkeys(FIELD_MAP, "")
def annotate_tasks(tasks):
    for task in tasks:
        task.update(resolve_responsibility(task))
    return tasks
