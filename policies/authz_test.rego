package pramana.authz_test

import data.pramana.authz

roles := {
	"employee": {"max_rank_own_dept": 1, "max_rank_other_dept": 1, "permissions": ["query:run"]},
	"manager": {"max_rank_own_dept": 2, "max_rank_other_dept": 1, "permissions": ["query:run"]},
	"analyst": {"max_rank_own_dept": 2, "max_rank_other_dept": 2, "permissions": ["query:run"]},
	"executive": {
		"max_rank_own_dept": 3, "max_rank_other_dept": 3,
		"permissions": ["query:run", "pii:read", "region:all"],
	},
	"admin": {"max_rank_own_dept": 1, "max_rank_other_dept": 1, "permissions": ["users:manage"]},
}

config := {"egress_max_rank": 2}

user(role, dept, region) := {
	"id": "u1", "role": role, "department": dept,
	"extra_departments": [], "region": region, "permissions": [],
}

doc(dept, rank, region) := {"id": "d1", "department": dept, "sensitivity_rank": rank, "region": region}

decide(u, r) if {
	authz.allow with input as {"user": u, "resource": r}
		with data.pramana.roles as roles
		with data.pramana.config as config
}

# ── Employees ───────────────────────────────────────────────────────────────
test_employee_reads_public_and_internal if {
	decide(user("employee", "engineering", "emea"), doc("hr", 0, "global"))
	decide(user("employee", "engineering", "emea"), doc("hr", 1, "global"))
}

test_employee_denied_confidential_even_in_own_department if {
	not decide(user("employee", "hr", "emea"), doc("hr", 2, "global"))
}

test_employee_denied_restricted if {
	not decide(user("employee", "finance", "emea"), doc("finance", 3, "global"))
}

# ── Managers ────────────────────────────────────────────────────────────────
test_manager_reads_confidential_in_own_department if {
	decide(user("manager", "hr", "emea"), doc("hr", 2, "global"))
}

test_manager_denied_confidential_in_other_department if {
	not decide(user("manager", "hr", "emea"), doc("finance", 2, "global"))
}

test_manager_denied_restricted_in_own_department if {
	not decide(user("manager", "hr", "emea"), doc("hr", 3, "global"))
}

test_extra_department_counts_as_own if {
	u := object.union(user("manager", "hr", "emea"), {"extra_departments": ["finance"]})
	decide(u, doc("finance", 2, "global"))
}

# ── Analysts ────────────────────────────────────────────────────────────────
test_analyst_reads_confidential_across_departments if {
	decide(user("analyst", "security", "apac"), doc("finance", 2, "global"))
}

test_analyst_denied_restricted if {
	not decide(user("analyst", "security", "apac"), doc("security", 3, "global"))
}

# ── Executives ──────────────────────────────────────────────────────────────
test_executive_reads_restricted_everywhere if {
	decide(user("executive", "company", "amer"), doc("hr", 3, "apac"))
}

# ── Administrators run the system but do not read sensitive content ─────────
test_admin_denied_confidential if {
	not decide(user("admin", "it", "amer"), doc("it", 2, "global"))
}

# ── Region (ABAC) ───────────────────────────────────────────────────────────
test_region_mismatch_denied if {
	not decide(user("analyst", "security", "apac"), doc("hr", 1, "emea"))
}

test_region_match_allowed if {
	decide(user("analyst", "security", "apac"), doc("hr", 1, "apac"))
}

# ── Fail closed ─────────────────────────────────────────────────────────────
test_unknown_role_denied if {
	not decide(user("intern", "hr", "emea"), doc("hr", 0, "global"))
}

test_missing_user_denied if {
	not authz.allow with input as {"resource": doc("hr", 0, "global")}
		with data.pramana.roles as roles
		with data.pramana.config as config
}

test_missing_resource_fields_denied if {
	not decide(user("executive", "company", "amer"), {"id": "d1"})
}

test_no_policy_data_denied if {
	not authz.allow with input as {
		"user": user("executive", "company", "amer"),
		"resource": doc("hr", 0, "global"),
	}
}

# ── A role claimed inside the request cannot widen access ───────────────────
test_role_limits_come_from_data_not_input if {
	u := object.union(
		user("employee", "hr", "emea"),
		{"max_rank_own_dept": 3, "max_rank_other_dept": 3, "clearance_level": 4},
	)
	not decide(u, doc("hr", 3, "global"))
}

# ── Scope ───────────────────────────────────────────────────────────────────
test_scope_for_manager if {
	s := authz.scope with input as {"user": user("manager", "hr", "emea")}
		with data.pramana.roles as roles
		with data.pramana.config as config
	s.max_rank_own_dept == 2
	s.max_rank_other_dept == 1
	s.departments == ["hr"]
	s.regions == ["emea", "global"]
	s.all_regions == false
	s.can_read_pii == false
}

test_scope_for_executive_has_all_regions_and_pii if {
	s := authz.scope with input as {"user": user("executive", "company", "amer")}
		with data.pramana.roles as roles
		with data.pramana.config as config
	s.all_regions == true
	s.can_read_pii == true
}

test_scope_undefined_for_unknown_role if {
	not authz.scope with input as {"user": user("intern", "hr", "emea")}
		with data.pramana.roles as roles
		with data.pramana.config as config
}

# ── Batch ───────────────────────────────────────────────────────────────────
test_batch_returns_only_allowed_ids if {
	ids := authz.allowed_ids with input as {
		"user": user("manager", "hr", "emea"),
		"resources": [
			{"id": "a", "department": "hr", "sensitivity_rank": 2, "region": "global"},
			{"id": "b", "department": "finance", "sensitivity_rank": 2, "region": "global"},
			{"id": "c", "department": "finance", "sensitivity_rank": 1, "region": "apac"},
			{"id": "d", "department": "legal", "sensitivity_rank": 0, "region": "global"},
		],
	}
		with data.pramana.roles as roles
		with data.pramana.config as config
	ids == ["a", "d"]
}

# ── Actions ─────────────────────────────────────────────────────────────────
test_action_allowed_by_role_permission if {
	authz.allow_action with input as {"user": user("admin", "it", "amer"), "action": "users:manage"}
		with data.pramana.roles as roles
}

test_action_denied_without_permission if {
	not authz.allow_action with input as {"user": user("employee", "hr", "emea"), "action": "users:manage"}
		with data.pramana.roles as roles
}

# ── Egress ──────────────────────────────────────────────────────────────────
test_egress_blocked_above_ceiling if {
	not authz.egress_allowed with input as {"resource": doc("hr", 3, "global")}
		with data.pramana.config as config
}

test_egress_allowed_at_ceiling if {
	authz.egress_allowed with input as {"resource": doc("hr", 2, "global")}
		with data.pramana.config as config
}
