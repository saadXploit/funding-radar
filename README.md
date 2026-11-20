# Funding Radar

**Who would actually fund my startup?** Type one line about your startup and get back a ranked list of investors to approach, with proof for every match.

Built for the TinyFish Student Bounty "Funding Radar".

## How it works

1. **Research.** TinyFish Research API (deep mode) finds 15–30 startups similar to yours that raised in the last 12–18 months, along with every named investor, the lead investor, the amount, the stage and source links.
2. **Search.** Deals that came back without a source get one from TinyFish Search, using news results limited to the time window.
3. **Verify.** TinyFish Fetch loads every source page live. An investor counts as *verified* only when its name appears on the page together with the company.
4. **Rank.** Each investor's score adds up its similar deals, weighted by:
   - similarity to your startup
   - how recent the deal was (newer counts more)
   - stage (pre-seed and seed count more for an early founder)
   - whether it **led** the round (×1.5)
   - whether it was **verified** on a live page (×1.25 if verified, ×0.8 if not)

## Run it

Python 3.9+ only. No packages to install.

```bash
export TINYFISH_API_KEY=your_key        # from agent.tinyfish.ai/api-keys
python funding_radar.py "AI bookkeeping assistant for small shops in Africa"
python funding_radar.py "..." --months 12 --top 10 --out my_radar
python funding_radar.py --demo          # offline demo with sample (fictional) data
```

Each run writes the results three ways: `*.md`, `*.html` (a shareable report page) and `*.json` (raw data for other tools).

## Tests

```bash
python -m pytest -q
```

## Author

Abdulgafar Saad, Founder of SAAD AI Automation · [@saad_abdulgafar](https://x.com/saad_abdulgafar)
