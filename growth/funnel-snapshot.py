"""Self-serve funnel snapshot, read from production over `growth/run-funnel-snapshot.sh`.

Read-only; aggregates only — no emails, no keys. Companion to selfserve-baseline.sql:
that report answers "how many signed up and activated"; this one adds where they came
from, who pays and whether they still use it, and whether the free limits are a wall
anyone actually hits before paying.
"""
import os
import psycopg2

c = psycopg2.connect(os.environ["DATABASE_URL"])
c.set_session(readonly=True)
cur = c.cursor()


def q(sql, *a):
    cur.execute(sql, a)
    return cur.fetchall()


print("== signups per week, last 12 weeks")
for r in q("select date_trunc('week',created_at)::date w, count(*) from users "
           "where created_at > now()-interval '84 days' group by 1 order by 1"):
    print("  ", r[0], r[1])

print("== signup_source, last 90 days")
for r in q("select coalesce(signup_source,'unknown'), count(*) from users "
           "where created_at > now()-interval '90 days' group by 1 order by 2 desc"):
    print("  ", r)

print("== funnel, 90-day cohort at least 14 days old, by source: signups, add7, search7, wk2, paid")
for r in q("""with c as (select id, created_at, coalesce(signup_source,'unknown') s from users
                 where created_at between now()-interval '90 days' and now()-interval '14 days'),
f as (select c.id, c.s, c.created_at,
 (select min(created_at) from usage_log l where l.user_id=c.id and action='add'
    and l.created_at between c.created_at and c.created_at+interval '7 days') a
 from c)
select s, count(*) signups, count(a) add7,
 count(*) filter (where a is not null and exists(select 1 from usage_log l where l.user_id=f.id
    and action in ('search','search_all') and l.created_at>f.a and l.created_at<f.created_at+interval '7 days')) srch7,
 count(*) filter (where exists(select 1 from usage_log l where l.user_id=f.id
    and l.created_at between f.created_at+interval '7 days' and f.created_at+interval '14 days')) wk2,
 count(*) filter (where exists(select 1 from subscriptions x where x.user_id=f.id and x.status='active' and x.plan<>'free')) paid
from f group by s order by 2 desc"""):
    print("  ", r)

print("== subscriptions by plan / status: count, first, last")
for r in q("select plan, status, count(*), min(created_at)::date, max(created_at)::date "
           "from subscriptions group by 1,2 order by 3 desc"):
    print("  ", r)

print("== paying users: source, signup date, sub start, days to pay, plan, usage events last 30d")
for r in q("""select coalesce(u.signup_source,'unknown'), u.created_at::date, s.created_at::date,
 (s.created_at::date - u.created_at::date) days, s.plan,
 (select count(*) from usage_log l where l.user_id=u.id and l.created_at>now()-interval '30 days') use30
 from subscriptions s join users u on u.id=s.user_id
 where s.status='active' and s.plan<>'free' order by s.created_at"""):
    print("  ", r)

print("== MAU / WAU (distinct users with any usage event)")
print("  ", q("select count(distinct user_id) filter (where created_at>now()-interval '30 days'), "
              "count(distinct user_id) filter (where created_at>now()-interval '7 days') from usage_log")[0])

print("== users who reached 40 adds in a calendar month (the free wall): total, paying now, ever had a subscription row")
for r in q("""with m as (select user_id, date_trunc('month',created_at) mo, count(*) adds from usage_log
                 where action='add' group by 1,2),
w as (select distinct user_id from m where adds>=40)
select count(*),
 count(*) filter (where exists(select 1 from subscriptions x where x.user_id=w.user_id and x.status='active' and x.plan<>'free')),
 count(*) filter (where exists(select 1 from subscriptions x where x.user_id=w.user_id)) from w"""):
    print("  ", r)

print("== users who reached 200 searches in a month: total, paying now")
for r in q("""with m as (select user_id, date_trunc('month',created_at) mo, count(*) n from usage_log
                 where action in ('search','search_all') group by 1,2),
w as (select distinct user_id from m where n>=200)
select count(*), count(*) filter (where exists(select 1 from subscriptions x where x.user_id=w.user_id
    and x.status='active' and x.plan<>'free')) from w"""):
    print("  ", r)

print("== checkout_sessions by status: total, last 90 days")
cur.execute("select column_name from information_schema.columns where table_name='checkout_sessions' order by ordinal_position")
cols = [r[0] for r in cur.fetchall()]
if "status" in cols:
    for r in q("select status, count(*), count(*) filter (where created_at>now()-interval '90 days') "
               "from checkout_sessions group by 1"):
        print("  ", r)
else:
    print("   (no status column:", cols, ")")

print("== paying accounts: days since their last usage event")
for r in q("""select s.plan, (now()::date - max(l.created_at)::date) days_silent
 from subscriptions s left join usage_log l on l.user_id=s.user_id
 where s.status='active' and s.plan<>'free' group by s.user_id, s.plan order by 2"""):
    print("  ", r)
