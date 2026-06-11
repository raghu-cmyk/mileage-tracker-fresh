# Road Ledger — Mileage Tracker

A web application for logging business mileage, managing vehicles, and generating year-end IRS standard mileage deduction summaries.

## Features

- **User authentication** — register, log in, log out with per-user data isolation
- **Trip logging** — create, view, edit, and delete trips with business/personal purpose
- **Vehicle management** — maintain a fleet with default vehicle selection and archival
- **IRS reports** — year-end business mileage summary with per-vehicle and monthly breakdowns

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export FLASK_APP=app.py
flask init-db
python app.py
```

Open http://127.0.0.1:5050 in your browser.

## Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

## IRS Mileage Rates

Standard mileage rates are configured in `app.py` (`IRS_MILEAGE_RATES`). Update annually when the IRS publishes new rates.
