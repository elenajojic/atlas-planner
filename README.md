# Atlas Planner

A Claude Desktop connector that plans University of Michigan schedules around **you**: what you've taken and how you did, where you're headed (premed, minors, research), what you're already committed to, and the time you want protected for everything else.

> **New:** run it as a free connector you add once in Claude (works on web, desktop and phone). See [HOSTING.md](HOSTING.md).
>
> **Status (v0.4):** reads live Atlas with your own login (default). Set `ATLAS_PLANNER_SOURCE=sample` to try it on fake data.

## How it works for a new user

1. Install the connector, then tell Claude "set up my Atlas planner".
2. It asks you to log into Atlas (Weblogin + Duo, in a normal browser window).
3. Claude walks you through setup one question at a time:
   career goals → majors/minors/premed → reviews what you've done and what's left (from your Atlas dashboard) →
   what to optimize for → class window → commitments (it can read your calendar) and protected time →
   graduation, MCAT term, spring/summer, study abroad.
4. Ask for a schedule. You get ranked options with a visual week grid and scorecard, plus a 4 year plan.

## What it compares

**Difficulty and grades**
- Workload %, average grade, and a 0 to 100 difficulty score for every course
- **Your predicted grade** as a range with a confidence level, based on how you did against the average in similar past courses (subject, topics, workload), adjusted if you tend to do better or worse in heavy courses
- Instructor grading vs the course average, and whether their grades and clarity are trending up or down
- Instructor quality: Atlas clarity cross checked against an optional second source, with disagreement flags
- Teaching style match: if you do better with clearer instructors, instructor quality counts extra for you
- Brutal combos: pairs that are rough together (from co enrollment data)
- Projected term, cumulative, and BCPM (premed science) GPA

**Long term**
- Workload balanced across all your semesters (minimizes the heaviest term)
- Only plans a course in a season it has historically run (offering confidence, e.g. "Winter 5 of 5 years")
- Double counting: prefers courses that satisfy major + minor + premed at once
- Critical path: gateway courses that unlock the most later ones go early
- MCAT timeline: MCAT courses finish before your test term
- Students like you: when BME premed students usually take each course
- Spring/summer terms (counted as twice as intense, since they're half length)
- Study abroad: tries every semester and ranks them by how little it disrupts your plan
- What if: drop a class, fail a class, add or drop a minor, move the MCAT, and see what changes
- Application story score and a semester heatmap

**Your week**
- Building to building walk times (Michigan time aware), and a 30 minute gap between North and Central
- Lunch window guaranteed every class day
- Day by day effort so no single day is a wall
- Seat risk (how fast sections fill) with backup sections that still fit
- Class size and format
- Protected time actually fits (e.g. 4h of volunteering in 2h blocks)
- The next term is always consistent with the 4 year plan, checked against real section times

**Friends mode**
- Export one option as a share file (course codes, sections and times only. No grades, no profile.)
- Your friend imports it: shared classes, matching sections, and common free time.

## Install (Claude Desktop)

**One click extension (for sharing)**
```bash
pip install -r requirements.txt --target lib
npx @anthropic-ai/mcpb pack            # creates atlas-planner.mcpb
```
Friends double click `atlas-planner.mcpb` to install.

**Manual**
```bash
pip install -r requirements.txt
python -m playwright install chromium   # only for live Atlas login
```
Add to Claude Desktop's `claude_desktop_config.json`, then restart:
```json
{
  "mcpServers": {
    "atlas-planner": {
      "command": "python3",
      "args": ["/full/path/to/atlas-planner/run.py"],
      "env": { "ATLAS_PLANNER_SOURCE": "atlas" }
    }
  }
}
```

## Privacy and rules of the road

- Everything lives in `~/.atlas-planner/` on your computer: profile, Atlas session (file mode 600), cache, views, share files.
- Sharing the connector shares code only. Each person uses their own login and data.
- Atlas data is student records data: personal planning only. Don't publish or redistribute it.
- Requests are cached for a week and spaced 1.5s apart.
- Rate My Professors' terms prohibit scraping, so it isn't scraped. You can paste ratings you looked up (`import_external_ratings`).
- Predicted grades and the story score are estimates. The premed template must be confirmed with UM Health Professions advising.
- It never registers you for classes.

## Tools

`data_status`, `atlas_login`, `setup_status`, `list_programs`, `refresh_atlas_data`, `get_progress`, `get_profile`, `update_profile`, `save_profile`, `import_external_ratings`, `search_offerings`, `course_report`, `compare_courses`, `optimize_schedule`, `plan_all_semesters`, `find_study_abroad_term`, `what_if_scenario`, `export_plan_for_friend`, `compare_with_friend`

## Live Atlas (v0.3)

Live Atlas is mapped and on by default (`ATLAS_PLANNER_SOURCE=atlas`). It reads, with your own login:
- **Your record:** an unofficial What If audit for up to 3 plans (major first). It covers completed, transfer/AP and in progress courses, plus your cumulative GPA. Atlas doesn't show letter grades, so add them under `my_grades` in your profile (for example `my_grades: {CHEM 130: A-}`) to get BCPM and grade predictions.
- **Requirements:** each audit line becomes a requirement group. Credit rules that don't name courses (Intellectual Breadth, 6 credits of 300 level BIOMEDE...) show in `get_progress` with a note, and you pick courses for them with Claude.
- **Courses:** catalog and offering history per subject, plus section times, rooms, seats and instructors for next term.
- **Evaluations:** workload, desire, understanding, expectations, grade distribution, and instructor clarity per course.

Engineering minors have one plan per major. For BME that's `AI BSE BioE` and `Business Admin BSE BioE`. Search with `list_programs(query=...)`.

The first `refresh_atlas_data` takes a few minutes, because requests are spaced 1.5s apart. After that, everything is cached for a week in `~/.atlas-planner/cache`. Delete that folder to force a refresh.

The endpoints are listed in the `AtlasSource` docstring in `planner/data_source.py`. If Atlas changes its site, that's the place to fix.

## Layout
```
planner/models.py        data types, terms, grade points
planner/data_source.py   sample + Atlas sources, cache, login
planner/buildings.py     building coordinates, walk times
planner/scoring.py       instructor cross check + grading trends, workload, difficulty
planner/grades.py        predicted grades, GPA/BCPM, teaching style
planner/requirements.py  programs -> requirement groups, double counting
planner/week.py          one term: sections, walk times, lunch, combos, seat risk
planner/longterm.py      all terms: offering history, MCAT, critical path, abroad, what if
planner/story.py         application story score
planner/friends.py       share files + comparison
planner/render.py        HTML week grid + semester heatmap
planner/server.py        MCP tools + guided setup
tools/gen_sample.py      regenerates the fake sample data
```
