#!/usr/bin/env python3
"""
Conversion emails to free users who hit their quota limits.
Run:  python3 scripts/send_conversion_emails.py                    # preview all
      python3 scripts/send_conversion_emails.py --send fede91it    # send one
      python3 scripts/send_conversion_emails.py --send all         # send all
"""
import argparse

RESEND_KEY = "re_918vn92M_GxZwVDVhQJZdDMeuN3BUUatv"
FROM = "Ali from Mengram <ali@mengram.io>"
REPLY_TO = "the.baizhanov@gmail.com"

EMAILS = {
    "carellarafael": {
        "to": "carellarafaelantonio@gmail.com",
        "subject": "You've hit your free plan limit",
        "body": """Hey Rafael,

I'm Ali, the founder of Mengram. You've reached the monthly limits on the free plan.

If you want to keep going, here are our plans:

- Starter ($5/mo) — 100 adds, 500 searches, webhooks, teams
- Pro ($19/mo) — 1,000 adds, 10,000 searches, reranking, smart triggers
- Growth ($59/mo) — 3,000 adds, 20,000 searches, unlimited agents
- Business ($99/mo) — 8,000 adds, 30,000 searches, unlimited teams

You can upgrade from the dashboard: https://mengram.io/dashboard#billing

Limits reset at the start of each month. Reply here if you have any questions.

Best,
Ali
Founder, Mengram""",
    },

    "kimseylok": {
        "to": "kimseylok@outlook.com",
        "subject": "You've hit your free plan limit",
        "body": """Hey,

I'm Ali, the founder of Mengram. You've reached the monthly limits on the free plan.

If you want to keep going, here are our plans:

- Starter ($5/mo) — 100 adds, 500 searches, webhooks, teams
- Pro ($19/mo) — 1,000 adds, 10,000 searches, reranking, smart triggers
- Growth ($59/mo) — 3,000 adds, 20,000 searches, unlimited agents
- Business ($99/mo) — 8,000 adds, 30,000 searches, unlimited teams

You can upgrade from the dashboard: https://mengram.io/dashboard#billing

Limits reset at the start of each month. Reply here if you have any questions.

Best,
Ali
Founder, Mengram""",
    },

    "fede91it": {
        "to": "fede91it@gmail.com",
        "subject": "You've hit your free plan limit",
        "body": """Hey,

I'm Ali, the founder of Mengram. You've reached the monthly limits on the free plan.

If you want to keep going, here are our plans:

- Starter ($5/mo) — 100 adds, 500 searches, webhooks, teams
- Pro ($19/mo) — 1,000 adds, 10,000 searches, reranking, smart triggers
- Growth ($59/mo) — 3,000 adds, 20,000 searches, unlimited agents
- Business ($99/mo) — 8,000 adds, 30,000 searches, unlimited teams

You can upgrade from the dashboard: https://mengram.io/dashboard#billing

Limits reset at the start of each month. Reply here if you have any questions.

Best,
Ali
Founder, Mengram""",
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", type=str, default=None,
                        help="Send to specific user key or 'all' (default: preview)")
    args = parser.parse_args()

    targets = EMAILS.keys() if not args.send or args.send == "all" else [args.send]

    for key in targets:
        if key not in EMAILS:
            print(f"Unknown user: {key}. Available: {', '.join(EMAILS.keys())}")
            continue

        email = EMAILS[key]
        print(f"{'='*60}")
        print(f"[{key}]")
        print(f"To: {email['to']}")
        print(f"Subject: {email['subject']}")
        print(f"Reply-To: {REPLY_TO}")
        print(f"---")
        print(email["body"])
        print()

        if args.send and (args.send == "all" or args.send == key):
            import resend, time
            resend.api_key = RESEND_KEY
            time.sleep(1)  # Resend rate limit: 2 req/sec
            r = resend.Emails.send({
                "from": FROM,
                "to": email["to"],
                "reply_to": REPLY_TO,
                "subject": email["subject"],
                "text": email["body"],
            })
            print(f"  >>> SENT: {r}")
        else:
            print("  [DRY RUN]")
        print()


if __name__ == "__main__":
    main()
