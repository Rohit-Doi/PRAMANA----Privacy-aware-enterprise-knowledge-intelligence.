# PRAMĀṆA access policy (RBAC + ABAC).
#
# The policy is pure logic. Everything that an administrator may change lives in data:
#   data.pramana.roles   {role_key: {max_rank_own_dept, max_rank_other_dept, permissions}}
#   data.pramana.config  {egress_max_rank, pseudonymize_names, show_withheld_count}
#
# Sensitivity ranks: 0 PUBLIC, 1 INTERNAL, 2 CONFIDENTIAL, 3 RESTRICTED.
#
# input.user      {id, role, department, extra_departments, region, permissions}
# input.resource  {id, department, sensitivity_rank, region}     (single decision)
# input.resources [resource, ...]                                (batch decision)
# input.action    permission string                              (action decision)
package pramana.authz

# ── Who is asking ───────────────────────────────────────────────────────────
# An unknown role leaves `role` undefined, which makes every rule below undefined
# and every decision fall back to its default: deny.
role := data.pramana.roles[input.user.role]

user_departments contains input.user.department

user_departments contains dept if some dept in input.user.extra_departments

permissions contains p if some p in role.permissions

permissions contains p if some p in input.user.permissions

all_regions if "region:all" in permissions

# ── Scope: the widest set of documents this user may read ───────────────────
# The backend compiles this object into the vector-store filter, the graph query
# and the SQL WHERE clause, so unauthorised content is never fetched at all.
scope := {
	"max_rank_own_dept": role.max_rank_own_dept,
	"max_rank_other_dept": role.max_rank_other_dept,
	"departments": sort([d | some d in user_departments]),
	"regions": scope_regions,
	"all_regions": all_regions_flag,
	"can_read_pii": "pii:read" in permissions,
}

all_regions_flag := true if {
	all_regions
} else := false

scope_regions := [] if {
	all_regions
} else := sort({"global", input.user.region})

# ── Single resource decision ────────────────────────────────────────────────
default allow := false

allow if readable(input.resource)

readable(resource) if {
	region_ok(resource)
	resource.sensitivity_rank <= role.max_rank_other_dept
}

readable(resource) if {
	region_ok(resource)
	resource.department in user_departments
	resource.sensitivity_rank <= role.max_rank_own_dept
}

region_ok(_) if all_regions

region_ok(resource) if resource.region == "global"

region_ok(resource) if resource.region == input.user.region

# Human-readable explanation, used by the policy simulator and the audit log.
reasons contains "unknown role: no access" if not role

reasons contains msg if {
	role
	not region_ok(input.resource)
	msg := sprintf("region %q is outside the user's regions", [input.resource.region])
}

reasons contains msg if {
	role
	region_ok(input.resource)
	not input.resource.department in user_departments
	input.resource.sensitivity_rank > role.max_rank_other_dept
	msg := sprintf(
		"sensitivity rank %d exceeds the limit %d for documents of other departments",
		[input.resource.sensitivity_rank, role.max_rank_other_dept],
	)
}

reasons contains msg if {
	role
	region_ok(input.resource)
	input.resource.department in user_departments
	input.resource.sensitivity_rank > role.max_rank_own_dept
	msg := sprintf(
		"sensitivity rank %d exceeds the limit %d for documents of the user's own department",
		[input.resource.sensitivity_rank, role.max_rank_own_dept],
	)
}

reasons contains "allowed: within the cross-department limit" if {
	region_ok(input.resource)
	input.resource.sensitivity_rank <= role.max_rank_other_dept
}

reasons contains "allowed: own department, within the own-department limit" if {
	region_ok(input.resource)
	input.resource.department in user_departments
	input.resource.sensitivity_rank <= role.max_rank_own_dept
	input.resource.sensitivity_rank > role.max_rank_other_dept
}

# ── Batch decision (defence in depth after retrieval) ───────────────────────
allowed_ids := [r.id | some r in input.resources; readable(r)]

# ── Action decision ─────────────────────────────────────────────────────────
default allow_action := false

allow_action if input.action in permissions

# ── LLM egress: may this content be sent to an external model? ──────────────
default egress_allowed := false

egress_allowed if input.resource.sensitivity_rank <= data.pramana.config.egress_max_rank

egress := {
	"max_rank": data.pramana.config.egress_max_rank,
	"pseudonymize_names": object.get(data.pramana.config, "pseudonymize_names", true),
}

show_withheld_count := object.get(data.pramana.config, "show_withheld_count", true)
