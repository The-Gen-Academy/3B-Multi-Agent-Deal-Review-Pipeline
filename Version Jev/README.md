# Deal Review — Version Jev

PDF extraction → parallel compliance, financial, and risk specialists →
synthesizer → Jev → report or human review.

Jev runs for every deal and chooses `HUMAN_REVIEW` or `GENERATE_REPORT`.
The panel supplies the loan decision; a human can override it. Confidence below
85%, an unresolved panel escalation, a missing key, or an API error also routes
to human review. Resuming uses the saved Jev result without another API call.

## Setup

1. Set `TYPESAFE_API_KEY` in `.env`. Use `.env.example` as the template.
2. Install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Start Ollama and pull the model:

   ```bash
   ollama serve
   ```

   In another terminal:

   ```bash
   ollama pull gemma4:e4b
   ```

## Run

From this folder:

```bash
streamlit run streamlit_app.py
```

Upload a PDF, run the pipeline, and submit a decision if human review is needed.
For the notebook, open `deal_review_jev.ipynb` with this folder as the working
directory. Run its cells in order; run the optional resume cell only after
reviewing a paused deal.

## Files

- `deal_review_jev.ipynb`: notebook pipeline and results.
- `streamlit_app.py`: dashboard and audit trail.
- `jev_review.py`: SDK request, two-choice routing, and fallback handling.
- `test_deal.pdf`: bundled notebook input.
