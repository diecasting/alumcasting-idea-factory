# alumcasting-idea-factory

> Content Opportunity Radar for manufacturing — an **offensive** (not spam)
> content-intelligence engine. It watches public conversations, finds real,
> heating-up manufacturing problems, and recommends English articles worth
> writing. No black-hat SEO, no fake content.

## Project purpose

A Content Opportunity Radar for:

- **Die Casting**
- **Aluminum Casting**
- **CNC Machining**
- **Powder Coating**
- **Manufacturing Problems** (porosity, cracking, warping, etc.)

The system answers:

1. What are manufacturing users discussing right now?
2. Which problems are heating up?
3. Which have clear **Problem Intent**?
4. Which have clear **Commercial Intent**?
5. Which deserve an English professional article?
6. Which topics should be written **first**?

## Architecture

```
Sources
  -> Collector
  -> Normalizer
  -> Deduplicator
  -> Problem Detector
  -> Scoring
  -> AI Topic Generator
  -> Opportunity Report
```

Phase 0 establishes the repository foundation and the shared data contracts.
The collector, detector, and AI stages are skeletons only.

### Scoring model (centralized in `app/processing/scoring.py`)

| Component          | Weight |
|--------------------|--------|
| Discussion Volume  | 25%    |
| Engagement         | 20%    |
| Problem Intent     | 20%    |
| Freshness          | 15%    |
| Commercial Intent  | 10%    |
| Content Gap        | 10%    |

Final score: **0–100**. Priority buckets:

- **P0** = 80–100
- **P1** = 60–79
- **P2** = 40–59
- **P3** = 0–39

## Repository layout

```
alumcasting-idea-factory/
├── .gitlab-ci.yml
├── README.md
├── requirements.txt
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── models.py            # Signal + Opportunity schemas
│   ├── pipeline.py          # Phase 0 smoke orchestration
│   ├── collectors/          # reddit.py, news.py (skeletons)
│   ├── processing/          # normalize, deduplicate, scoring
│   └── ai/                  # topic_generator.py (skeleton)
├── config/
│   └── keywords.yml         # English keyword taxonomy v1
├── reports/                 # CI artifact output (gitignored except .gitkeep)
└── tests/
    └── test_smoke.py
```

## Current status

**Phase 0 — Repository Foundation**
**Status: COMPLETE**

Repository foundation, unified data models, scoring framework, keyword
taxonomy, smoke pipeline, and CI are in place. Tests pass with zero secrets.

## Security

No credentials are committed. Redis/News/OpenAI access is provided through
**GitLab CI/CD variables** in later phases — never hardcoded here.

Prohibited (and never implemented): search-engine manipulation, spam,
keyword stuffing, cloaking, fake users/questions/news, or backlink spam.
This system only analyzes public information to surface real content needs.

## Development

```bash
pip install -r requirements.txt
pytest                      # run tests
python -m app.pipeline      # run the Phase 0 smoke pipeline
```
