"""Procurement topology is explicit; invitation never authorises negotiation."""
def apply_modular_procurement(tasks,facts):
    from core.procurement_policy import apply_three_round
    return apply_three_round(tasks,facts)
