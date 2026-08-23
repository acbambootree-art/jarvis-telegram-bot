# Apple Watch → Jarvis health sync

HealthKit has no cloud API, so the **phone pushes** once a day to:

```
POST https://jarvis-telegram-bot-9wyd.onrender.com/webhook/health
Header:  X-Admin-Secret: <TELEGRAM_WEBHOOK_SECRET from .env>
Body:    JSON (either shape below)
```

Append `?dry_run=1` while setting up — the server echoes what it *would*
store without writing anything, so you can see it parsed your payload
correctly before trusting it. Remove it when it looks right.

Data lands in the same `health_metrics` table Jarvis already reads, so
"how did I sleep this week?", the noon coach message and the state-read
synthesis all pick it up with no further work. A re-sync of the same
metric and day **replaces** what the sync wrote earlier (never anything
you logged by hand), so double-fires are harmless.

---

## Option A — Health Auto Export (recommended, ~5 min)

App Store: **Health Auto Export – JSON+CSV** (Lybron Sobers). Automations
need its paid tier; check the app for current pricing. It does the date
windows and sleep aggregation properly, which is exactly the part that is
fiddly to hand-roll.

1. Install, open, grant Health access to the metrics you want.
2. **Automations → + → REST API**
3. URL: `https://jarvis-telegram-bot-9wyd.onrender.com/webhook/health?dry_run=1`
4. Headers: `X-Admin-Secret` = your `TELEGRAM_WEBHOOK_SECRET`
5. Format **JSON** · Aggregate **Days** (one row per metric per day) ·
   Period **Previous day** (or "since last sync")
6. Metrics — start with: Steps, Heart Rate, Resting Heart Rate,
   Sleep Analysis, Active Energy, Walking + Running Distance. Add Weight,
   Body Fat, Blood Pressure, HRV, Blood Oxygen if you track them.
7. Schedule daily at **07:30** or later — after you are up, so last
   night's sleep is complete.
8. Run it once by hand. You should see
   `{"success": true, "dry_run": true, "would_store": [...]}`.
9. Remove `?dry_run=1` from the URL. Done.

HAE metric names are mapped onto Jarvis's types (`step_count → steps`,
`active_energy → calories`, `walking_running_distance → distance`, …).
Anything unmapped (VO₂ max, flights climbed, …) is stored under HAE's
own name, so nothing is dropped.

## Option B — iOS Shortcut (free)

Builds the flat shape: `{"date": "yyyy-MM-dd", "steps": N, "sleep": H, ...}`.
Every numeric key is a metric; `date` applies to all of them. Good for
steps / energy / distance / heart rate. **Sleep is the awkward one** — a
night spans midnight, so "today" and "yesterday" filters both cut it in
half; use "in the last 1 day" run in the morning for sleep only.

Run it on a **Time of Day** automation at ~07:30, reporting *yesterday*:

1. **Date** → **Adjust Date** (Get Start of Day) → `TodayStart`
2. **Adjust Date** `TodayStart` − 1 day → `YesterdayStart`
3. **Format Date** `YesterdayStart` custom `yyyy-MM-dd` → `DateStr`
4. For Steps, Active Energy, Walking + Running Distance:
   **Find Health Samples** type X, *Start Date is between* `YesterdayStart`
   and `TodayStart` → **Calculate Statistics** Sum → variable
5. Heart Rate: same filter → Calculate Statistics **Average**
6. Sleep: **Find Health Samples** Sleep Analysis, *Start Date is in the
   last 1 day*, *Value is not In Bed* → Get Details (Duration) → Calculate
   Statistics Sum → divide by 60 if the unit is minutes → `SleepHours`
7. **Dictionary**: `date`=`DateStr`, `steps`, `calories`, `distance`,
   `heart_rate`, `sleep`
8. **Get Contents of URL**: POST, the URL above (`?dry_run=1` at first),
   header `X-Admin-Secret`, Request Body = JSON = the dictionary
9. **Show Result** while testing, so you see the server's reply.

Exact action labels vary a little by iOS version; the server's dry-run
reply is the ground truth for whether the payload is right.

---

## Checking it worked

Ask Jarvis: *"what did I sleep last night?"* / *"steps this week?"* — or
run `/admin/diag` style check directly:

```bash
cd "/Users/c.jphua/AI assistant" && curl -s -X POST \
  "https://jarvis-telegram-bot-9wyd.onrender.com/webhook/health?dry_run=1" \
  -H "X-Admin-Secret: $(grep TELEGRAM_WEBHOOK_SECRET .env | cut -d= -f2)" \
  -H "content-type: application/json" \
  -d '{"date":"2026-08-17","steps":8432,"sleep":6.2}'
```

Responses: `401` wrong/missing secret · `400` with a reason for bad JSON,
no numeric readings, or an unparseable date · `200` with
`stored` / `replaced` / `metrics` / `days` on success.
