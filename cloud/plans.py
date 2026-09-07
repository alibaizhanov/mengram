"""Plan quotas — the single table the API, billing and quota emails read."""

PLAN_QUOTAS = {
    "free":     {"adds": 40,    "searches": 200,    "agents": 3,   "reflects": 3,   "dedups": 1,   "reindexes": 1,   "rules": 3,    "rate_limit": 20,  "webhooks": 0,  "teams": 0,  "sub_users": 3},
    "starter":  {"adds": 100,   "searches": 500,    "agents": 10,  "reflects": 30,  "dedups": 5,   "reindexes": 5,   "rules": 10,   "rate_limit": 60,  "webhooks": 2,  "teams": 1,  "sub_users": 10},
    "pro":      {"adds": 1_000, "searches": 10_000, "agents": 50,  "reflects": -1,  "dedups": 20,  "reindexes": 10,  "rules": -1,   "rate_limit": 120, "webhooks": 10, "teams": 5,  "sub_users": 50},
    "growth":   {"adds": 3_000, "searches": 20_000, "agents": -1,  "reflects": -1,  "dedups": 50,  "reindexes": 20,  "rules": -1,   "rate_limit": 200, "webhooks": 25, "teams": 10, "sub_users": 100},
    "business":   {"adds": 8_000, "searches": 30_000, "agents": -1,  "reflects": -1,  "dedups": -1,  "reindexes": -1,  "rules": -1,   "rate_limit": 300, "webhooks": 50, "teams": -1, "sub_users": -1},
    "selfhosted": {"adds": -1,    "searches": -1,     "agents": -1,  "reflects": -1,  "dedups": -1,  "reindexes": -1,  "rules": -1,   "rate_limit": 600, "webhooks": -1, "teams": -1, "sub_users": -1},
}
