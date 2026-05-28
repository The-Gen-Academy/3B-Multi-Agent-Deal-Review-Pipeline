# Deal Review Pipeline — V2

Multi-agent parallel pipeline with human-in-the-loop escalation, built with LangGraph + Ollama.

---

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running

---

## Setup

**1. Pull the model**
```bash
ollama serve
ollama pull gemma4:e4b
```

**2. Install dependencies**
```bash
pip install langchain langchain-ollama langgraph pypdf pydantic streamlit
```

---

## Run

```bash
cd DealReview/V2
streamlit run streamlit_app.py
```

Opens at `http://localhost:8501`

---

## Usage

1. Upload a deal PDF using the file uploader
2. Click **Run Pipeline**
3. Watch each agent run in real time
4. If the deal is escalated (amount ≥ $10M or low confidence), a human review form appears — choose Approve / Conditional Approve / Reject and add notes
5. Switch to the **Audit Trail** tab to see every agent's findings step by step

---

## Escalation Triggers

Any one of these pauses the pipeline for human sign-off:

| Condition | Threshold |
|---|---|
| Loan amount | ≥ $10,000,000 |
| Panel avg confidence | < 65% |
| AI decision | ESCALATE |

---

## Project Structure

```
V2/
├── streamlit_app.py   # All pipeline code + Streamlit UI
├── test_deal.pdf      # Sample deal document
└── README.md          # This file
```
