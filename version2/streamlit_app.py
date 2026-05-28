"""
Deal Review — V2  |  Streamlit Dashboard
Multi-agent parallel pipeline with HITL
"""

# ── Standard library ────────────────────────────────────────────────
import operator
import json
import uuid
import datetime
import tempfile
import os
from typing import Annotated, TypedDict, Literal

# ── LangChain / LangGraph ────────────────────────────────────────────
from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.errors import GraphInterrupt

# ── Pydantic ─────────────────────────────────────────────────────────
from pydantic import BaseModel, Field

# ── PDF ──────────────────────────────────────────────────────────────
from pypdf import PdfReader

# ── Streamlit ────────────────────────────────────────────────────────
import streamlit as st

# ════════════════════════════════════════════════════════════════════
# PAGE CONFIG  (must be first Streamlit call)
# ════════════════════════════════════════════════════════════════════
st.set_page_config(layout="wide", page_title="Deal Review — V2", page_icon="🏦")

# ════════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS  (replicated exactly from notebook)
# ════════════════════════════════════════════════════════════════════

class DealTerms(BaseModel):
    borrower:      str   = Field(description="Borrower / obligor name")
    amount_usd:    float = Field(description="Total loan amount in USD")
    ltv:           float = Field(description="Loan-to-value ratio as decimal (e.g. 0.75)")
    dti:           float = Field(description="Debt-to-income ratio as decimal")
    credit_score:  int   = Field(description="FICO / credit score 300-850")
    rate_pct:      float = Field(description="Annual interest rate percentage")
    term_months:   int   = Field(description="Loan term in months")
    jurisdiction:  str   = Field(description="Borrower jurisdiction or state")
    property_type: str   = Field(description="Asset or property type")
    purpose:       str   = Field(description="Stated loan purpose")


class ComplianceFindings(BaseModel):
    status:           Literal["PASS", "FAIL", "CONDITIONAL"]
    failed_rules:     list[str] = Field(default_factory=list)
    regulatory_notes: list[str] = Field(default_factory=list)
    confidence:       float     = Field(ge=0.0, le=1.0)
    reasoning:        str


class FinancialFindings(BaseModel):
    ltv_assessment:   str
    dti_assessment:   str
    rate_assessment:  str
    financial_health: Literal["STRONG", "ADEQUATE", "WEAK", "CRITICAL"]
    strengths:        list[str] = Field(default_factory=list)
    concerns:         list[str] = Field(default_factory=list)
    confidence:       float     = Field(ge=0.0, le=1.0)
    reasoning:        str


class RiskFindings(BaseModel):
    credit_risk_score:        float = Field(ge=0, le=100)
    concentration_risk_score: float = Field(ge=0, le=100)
    composite_risk_score:     float = Field(ge=0, le=100)
    risk_level:               Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    mitigants:                list[str] = Field(default_factory=list)
    confidence:               float     = Field(ge=0.0, le=1.0)
    reasoning:                str


class PanelDecision(BaseModel):
    decision:            Literal["APPROVE", "CONDITIONAL_APPROVE", "REJECT", "ESCALATE"]
    overall_risk_score:  float     = Field(ge=0, le=100)
    requires_escalation: bool
    conditions:          list[str] = Field(default_factory=list)
    key_risks:           list[str] = Field(default_factory=list)
    executive_summary:   str
    panel_confidence:    float     = Field(ge=0.0, le=1.0)


# ════════════════════════════════════════════════════════════════════
# STATE
# ════════════════════════════════════════════════════════════════════

class SpecialistResult(TypedDict):
    specialist: str
    findings:   dict
    confidence: float


class DealState(TypedDict):
    deal_id:             str
    deal_text:           str
    terms:               DealTerms | None
    specialist_results:  Annotated[list[SpecialistResult], operator.add]
    panel_decision:      PanelDecision | None
    requires_escalation: bool
    human_decision:      str | None
    human_notes:         str | None
    final_report:        dict | None


# ════════════════════════════════════════════════════════════════════
# CONFIGURATION + RULES
# ════════════════════════════════════════════════════════════════════

MODEL = "gemma4:e4b"

RULES = {
    "max_loan_usd":          50_000_000,
    "max_ltv":               0.80,
    "min_credit_score":      620,
    "max_dti":               0.43,
    "max_rate_pct":          18.0,
    "escalation_usd":        10_000_000,
    "blocked_jurisdictions": ["OFAC", "FATF-blacklist"],
}


# ════════════════════════════════════════════════════════════════════
# TOOLS  (replicated exactly from notebook)
# ════════════════════════════════════════════════════════════════════

class CreditRiskInput(BaseModel):
    credit_score: int   = Field(description="FICO credit score 300-850")
    ltv:          float = Field(description="Loan-to-value ratio 0-1")
    dti:          float = Field(description="Debt-to-income ratio 0-1")
    rate_pct:     float = Field(description="Annual interest rate percentage")


@tool(args_schema=CreditRiskInput)
def calc_credit_risk(credit_score: int, ltv: float, dti: float, rate_pct: float) -> str:
    """Calculate composite credit risk score 0–100 (higher = riskier). Use with borrower metrics."""
    score_pen = max(0.0, (750 - credit_score) / 130) * 40
    ltv_pen   = max(0.0, (ltv   - 0.60) / 0.20)      * 25
    dti_pen   = max(0.0, (dti   - 0.35) / 0.08)      * 20
    rate_pen  = max(0.0, (rate_pct - 6.0) / 12.0)    * 15
    score     = round(min(100.0, score_pen + ltv_pen + dti_pen + rate_pen), 1)
    return json.dumps({"credit_risk_score": score,
                       "breakdown": {"credit": round(score_pen, 1), "ltv": round(ltv_pen, 1),
                                     "dti": round(dti_pen, 1), "rate": round(rate_pen, 1)}})


class ConcentrationRiskInput(BaseModel):
    amount_usd:          float = Field(description="Loan amount in USD")
    portfolio_limit_usd: float = Field(default=500_000_000, description="Portfolio limit in USD")


@tool(args_schema=ConcentrationRiskInput)
def calc_concentration_risk(amount_usd: float, portfolio_limit_usd: float = 500_000_000) -> str:
    """Calculate concentration risk as percentage of total portfolio."""
    pct   = (amount_usd / portfolio_limit_usd) * 100
    score = round(min(100.0, pct * 5), 1)
    tier  = "HIGH" if score > 60 else "MEDIUM" if score > 30 else "LOW"
    return json.dumps({"concentration_pct": round(pct, 2),
                       "concentration_risk_score": score, "tier": tier})


RISK_TOOLS = [calc_credit_risk, calc_concentration_risk]


# ════════════════════════════════════════════════════════════════════
# NODES  (replicated exactly from notebook)
# ════════════════════════════════════════════════════════════════════

def node_extract(state: DealState) -> dict:
    llm = ChatOllama(model=MODEL, temperature=0, num_ctx=8192)
    structured = llm.with_structured_output(DealTerms)
    terms = structured.invoke([
        SystemMessage(content=(
            "You are a financial document analyst. Extract all loan terms precisely. "
            "Ratios must be decimals (0.75 not 75%). If a field is missing, make a "
            "reasonable inference from context."
        )),
        HumanMessage(content=f"Extract terms from:\n\n{state['deal_text'][:6000]}"),
    ])
    return {
        "terms": terms,
        "specialist_results":  [],
        "requires_escalation": False,
        "human_decision":      None,
        "human_notes":         None,
        "panel_decision":      None,
        "final_report":        None,
    }


def node_compliance_specialist(state: DealState) -> dict:
    terms = state["terms"]
    failed_rules     = []
    regulatory_notes = []

    if terms.ltv > RULES["max_ltv"]:
        failed_rules.append(f"LTV {terms.ltv:.1%} exceeds max {RULES['max_ltv']:.0%}")
    if terms.dti > RULES["max_dti"]:
        failed_rules.append(f"DTI {terms.dti:.1%} exceeds max {RULES['max_dti']:.0%}")
    if terms.credit_score < RULES["min_credit_score"]:
        failed_rules.append(f"Credit score {terms.credit_score} below min {RULES['min_credit_score']}")
    if terms.rate_pct > RULES["max_rate_pct"]:
        failed_rules.append(f"Rate {terms.rate_pct}% exceeds max {RULES['max_rate_pct']}%")
    if terms.amount_usd > RULES["max_loan_usd"]:
        failed_rules.append(f"Amount ${terms.amount_usd:,.0f} exceeds max ${RULES['max_loan_usd']:,.0f}")
    if any(b in terms.jurisdiction.upper() for b in RULES["blocked_jurisdictions"]):
        failed_rules.append(f"Jurisdiction '{terms.jurisdiction}' is blocked")

    if terms.amount_usd >= RULES["escalation_usd"]:
        regulatory_notes.append("Amount exceeds escalation threshold — requires senior review")
    if terms.ltv > 0.75:
        regulatory_notes.append("LTV above 75% — enhanced due diligence applies")

    status = "FAIL" if failed_rules else ("CONDITIONAL" if regulatory_notes else "PASS")

    findings = ComplianceFindings(
        status=status,
        failed_rules=failed_rules,
        regulatory_notes=regulatory_notes,
        confidence=1.0,
        reasoning=(
            f"Rule-based check against {len(RULES)} policy thresholds. "
            f"{len(failed_rules)} violation(s) found."
        ),
    )
    return {"specialist_results": [{"specialist": "compliance",
                                    "findings": findings.model_dump(),
                                    "confidence": findings.confidence}]}


def node_financial_analyst(state: DealState) -> dict:
    llm = ChatOllama(model=MODEL, temperature=0, num_ctx=8192)
    terms = state["terms"]
    structured = llm.with_structured_output(FinancialFindings)
    findings = structured.invoke([
        SystemMessage(content=(
            "You are a senior loan underwriter. Assess the financial health of this application.\n"
            "Benchmarks:\n"
            "  LTV : <0.65 STRONG | 0.65-0.75 ADEQUATE | 0.75-0.80 BORDERLINE | >0.80 WEAK\n"
            "  DTI : <0.36 STRONG | 0.36-0.43 ADEQUATE | 0.43-0.50 WEAK | >0.50 CRITICAL\n"
            "  Credit: >740 STRONG | 700-740 GOOD | 660-700 FAIR | 620-660 MARGINAL\n"
            "Be specific about strengths and concerns."
        )),
        HumanMessage(content=f"Loan terms:\n{json.dumps(terms.model_dump(), indent=2)}"),
    ])
    return {"specialist_results": [{"specialist": "financial",
                                    "findings": findings.model_dump(),
                                    "confidence": findings.confidence}]}


def node_risk_quantifier(state: DealState) -> dict:
    llm = ChatOllama(model=MODEL, temperature=0, num_ctx=8192)
    terms = state["terms"]
    agent  = create_react_agent(llm, RISK_TOOLS, response_format=RiskFindings)
    result = agent.invoke({"messages": [SystemMessage(content=(
        f"You are a quantitative risk analyst.\n"
        f"Loan terms: {json.dumps(terms.model_dump(), indent=2)}\n\n"
        "Step 1: Call calc_credit_risk with the borrower's metrics.\n"
        "Step 2: Call calc_concentration_risk with the loan amount.\n"
        "Step 3: Composite = 60% credit + 40% concentration.\n"
        "        Risk levels: LOW (<25) MEDIUM (25-50) HIGH (50-75) CRITICAL (>75)."
    ))]})
    findings = result["structured_response"]
    return {"specialist_results": [{"specialist": "risk",
                                    "findings": findings.model_dump(),
                                    "confidence": findings.confidence}]}


def node_synthesize(state: DealState) -> dict:
    llm = ChatOllama(model=MODEL, temperature=0, num_ctx=8192)
    results  = state["specialist_results"]
    terms    = state["terms"]
    by_type  = {r["specialist"]: r["findings"] for r in results}
    avg_conf = sum(r["confidence"] for r in results) / max(len(results), 1)

    structured = llm.with_structured_output(PanelDecision)
    decision   = structured.invoke([
        SystemMessage(content=(
            "You are the chair of a credit committee. Based on the specialist panel, issue a final decision.\n"
            "Decision guide:\n"
            "  APPROVE            — all panels positive, risk <35, compliance PASS\n"
            "  CONDITIONAL_APPROVE — minor issues, conditions can mitigate risk\n"
            "  ESCALATE           — amount >$10M, or avg confidence <0.65, or conflicting signals\n"
            "  REJECT             — compliance FAIL, risk >75, or fundamental underwriting flaws"
        )),
        HumanMessage(content=(
            f"Deal terms:\n{json.dumps(terms.model_dump(), indent=2)}\n\n"
            f"Compliance panel:\n{json.dumps(by_type.get('compliance', {}), indent=2)}\n\n"
            f"Financial panel:\n{json.dumps(by_type.get('financial', {}), indent=2)}\n\n"
            f"Risk panel:\n{json.dumps(by_type.get('risk', {}), indent=2)}\n\n"
            f"Panel average confidence: {avg_conf:.0%}\n"
            "Issue your credit committee decision."
        )),
    ])

    escalate = (
        decision.requires_escalation
        or decision.decision == "ESCALATE"
        or terms.amount_usd >= RULES["escalation_usd"]
        or avg_conf < 0.65
    )
    return {"panel_decision": decision, "requires_escalation": escalate}


def node_human_review(state: DealState) -> dict:
    terms    = state["terms"]
    decision = state["panel_decision"]
    packet   = {
        "deal_id":    state["deal_id"],
        "borrower":   terms.borrower,
        "amount_usd": terms.amount_usd,
        "ai_decision": decision.decision,
        "risk_score":  decision.overall_risk_score,
        "confidence":  decision.panel_confidence,
        "summary":     decision.executive_summary,
        "key_risks":   decision.key_risks,
        "conditions":  decision.conditions,
        "prompt":      "Enter: APPROVE | REJECT | CONDITIONAL_APPROVE  (optionally add '| notes')",
    }
    human_input = interrupt(packet)

    if isinstance(human_input, dict):
        h_decision = human_input.get("decision", decision.decision).upper()
        h_notes    = human_input.get("notes", "")
    else:
        parts      = str(human_input).strip().split("|", 1)
        h_decision = parts[0].strip().upper()
        h_notes    = parts[1].strip() if len(parts) > 1 else ""

    return {"human_decision": h_decision, "human_notes": h_notes}


def node_generate_report(state: DealState) -> dict:
    terms    = state["terms"]
    decision = state["panel_decision"]
    results  = state["specialist_results"]
    by_type  = {r["specialist"]: r["findings"] for r in results}
    final_decision = state.get("human_decision") or decision.decision

    report = {
        "deal_id":   state["deal_id"],
        "timestamp": datetime.datetime.now().isoformat(),
        "deal_summary": terms.model_dump(),
        "specialist_panel": {
            "compliance": by_type.get("compliance"),
            "financial":  by_type.get("financial"),
            "risk":       by_type.get("risk"),
        },
        "ai_recommendation": {
            "decision":          decision.decision,
            "overall_risk":      decision.overall_risk_score,
            "confidence":        decision.panel_confidence,
            "conditions":        decision.conditions,
            "key_risks":         decision.key_risks,
            "executive_summary": decision.executive_summary,
        },
        "final_outcome": {
            "decision":       final_decision,
            "escalated":      state["requires_escalation"],
            "human_override": state.get("human_decision") is not None,
            "human_notes":    state.get("human_notes"),
        },
    }
    return {"final_report": report}


# ════════════════════════════════════════════════════════════════════
# GRAPH  (replicated exactly from notebook — cached so MemorySaver persists)
# ════════════════════════════════════════════════════════════════════

def fan_out_to_specialists(state: DealState) -> list[Send]:
    return [
        Send("compliance_specialist", state),
        Send("financial_analyst",     state),
        Send("risk_quantifier",       state),
    ]


def route_post_synthesis(state: DealState) -> str:
    return "human_review" if state["requires_escalation"] else "generate_report"


@st.cache_resource
def build_graph():
    checkpointer = MemorySaver()
    builder      = StateGraph(DealState)

    builder.add_node("extract_terms",         node_extract)
    builder.add_node("compliance_specialist", node_compliance_specialist)
    builder.add_node("financial_analyst",     node_financial_analyst)
    builder.add_node("risk_quantifier",       node_risk_quantifier)
    builder.add_node("synthesize",            node_synthesize)
    builder.add_node("human_review",          node_human_review)
    builder.add_node("generate_report",       node_generate_report)

    builder.add_edge(START, "extract_terms")
    builder.add_conditional_edges(
        "extract_terms",
        fan_out_to_specialists,
        ["compliance_specialist", "financial_analyst", "risk_quantifier"],
    )
    builder.add_edge("compliance_specialist", "synthesize")
    builder.add_edge("financial_analyst",     "synthesize")
    builder.add_edge("risk_quantifier",       "synthesize")
    builder.add_conditional_edges(
        "synthesize",
        route_post_synthesis,
        ["human_review", "generate_report"],
    )
    builder.add_edge("human_review",    "generate_report")
    builder.add_edge("generate_report", END)

    return builder.compile(checkpointer=checkpointer), checkpointer


# ════════════════════════════════════════════════════════════════════
# HELPER — UI colours
# ════════════════════════════════════════════════════════════════════

def decision_color(d: str) -> str:
    return {
        "APPROVE":             "#22c55e",
        "CONDITIONAL_APPROVE": "#f97316",
        "REJECT":              "#ef4444",
        "ESCALATE":            "#eab308",
    }.get(d, "#6b7280")


def compliance_color(s: str) -> str:
    return {"PASS": "#22c55e", "CONDITIONAL": "#f97316", "FAIL": "#ef4444"}.get(s, "#6b7280")


def health_color(h: str) -> str:
    return {"STRONG": "#22c55e", "ADEQUATE": "#3b82f6",
            "WEAK": "#f97316",   "CRITICAL": "#ef4444"}.get(h, "#6b7280")


def risk_color(r: str) -> str:
    return {"LOW": "#22c55e", "MEDIUM": "#eab308",
            "HIGH": "#f97316", "CRITICAL": "#ef4444"}.get(r, "#6b7280")


def badge(label: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#fff;padding:4px 14px;'
        f'border-radius:9999px;font-weight:700;font-size:0.9rem;">{label}</span>'
    )


def conf_bar(value: float, label: str = "Confidence") -> None:
    st.caption(f"{label}: {value:.0%}")
    st.progress(value)


# ════════════════════════════════════════════════════════════════════
# SESSION-STATE INITIALISATION
# ════════════════════════════════════════════════════════════════════

for key, default in [
    ("pipeline_result",    None),
    ("graph_config",       None),
    ("deal_id",            None),
    ("escalation_pending", False),
    ("run_complete",       False),
    ("escalation_packet",  None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ════════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("⚙️ Configuration")

    st.subheader("Model")
    st.code(MODEL, language=None)
    st.markdown(f"**Temperature:** 0")
    st.markdown(f"**Context window:** 8 192 tokens")

    st.divider()
    st.subheader("Policy Rules")

    rules_display = [
        ("Max Loan",          f"${RULES['max_loan_usd']:,.0f}"),
        ("Max LTV",           f"{RULES['max_ltv']:.0%}"),
        ("Min Credit Score",  str(RULES["min_credit_score"])),
        ("Max DTI",           f"{RULES['max_dti']:.0%}"),
        ("Max Rate",          f"{RULES['max_rate_pct']}%"),
        ("Escalation ≥",      f"${RULES['escalation_usd']:,.0f}"),
        ("Blocked Juris.",    ", ".join(RULES["blocked_jurisdictions"])),
    ]

    for name, val in rules_display:
        col_n, col_v = st.columns([3, 2])
        col_n.markdown(f"**{name}**")
        col_v.markdown(val)

    st.divider()
    st.subheader("About")
    st.info("Multi-agent parallel pipeline with HITL")
    st.markdown(
        "Three specialists run **in parallel** after extraction. "
        "A synthesizer aggregates findings. Deals above the escalation "
        "threshold pause for **Human-in-the-Loop** review via LangGraph `interrupt()`."
    )


# ════════════════════════════════════════════════════════════════════
# MAIN AREA — TWO TABS
# ════════════════════════════════════════════════════════════════════

st.title("🏦 Deal Review — V2")
tab_run, tab_audit = st.tabs(["🏦 Run Pipeline", "📋 Audit Trail"])


# ────────────────────────────────────────────────────────────────────
# TAB 1 — RUN PIPELINE
# ────────────────────────────────────────────────────────────────────

with tab_run:

    # Ollama connectivity hint
    st.info(
        "Ollama must be running: `ollama serve && ollama pull gemma4:e4b`",
        icon="ℹ️",
    )

    uploaded = st.file_uploader("Upload Deal PDF", type=["pdf"], label_visibility="collapsed")
    run_btn  = st.button("▶ Run Pipeline", disabled=(uploaded is None), type="primary")

    # ── Kick off a fresh run ────────────────────────────────────────
    if run_btn and uploaded is not None:
        # Reset session state for a new run
        st.session_state.pipeline_result    = None
        st.session_state.escalation_pending = False
        st.session_state.run_complete       = False
        st.session_state.escalation_packet  = None

        # Read PDF text
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(uploaded.read())
            tmp_path = tmp.name

        try:
            reader    = PdfReader(tmp_path)
            deal_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        finally:
            os.unlink(tmp_path)

        deal_id = f"DEAL-{uuid.uuid4().hex[:6].upper()}"
        config  = {"configurable": {"thread_id": deal_id}}

        st.session_state.deal_id      = deal_id
        st.session_state.graph_config = config

        initial: DealState = {
            "deal_id":             deal_id,
            "deal_text":           deal_text,
            "terms":               None,
            "specialist_results":  [],
            "panel_decision":      None,
            "requires_escalation": False,
            "human_decision":      None,
            "human_notes":         None,
            "final_report":        None,
        }

        graph, _ = build_graph()

        with st.status("Running pipeline…", expanded=True) as status:
            try:
                st.write(f"Deal ID: **{deal_id}** — PDF loaded ({len(deal_text):,} chars)")
                st.write("📄 Step 1/5 — Extracting deal terms…")

                # Stream events so we can update the status in real time
                final_state = None
                for event in graph.stream(initial, config=config, stream_mode="updates"):
                    node_name = next(iter(event))
                    if node_name == "extract_terms":
                        st.write("⚖️📊🎲 Step 2/5 — Running parallel specialist panel…")
                    elif node_name in ("compliance_specialist", "financial_analyst", "risk_quantifier"):
                        labels = {"compliance_specialist": "⚖️ Compliance",
                                  "financial_analyst":     "📊 Financial Analyst",
                                  "risk_quantifier":       "🎲 Risk Quantifier"}
                        st.write(f"  {labels[node_name]} complete")
                    elif node_name == "synthesize":
                        st.write("🔮 Step 3/5 — Synthesizer aggregating panel…")
                    elif node_name == "human_review":
                        st.write("👤 Step 4/5 — Pausing for human review…")
                    elif node_name == "generate_report":
                        st.write("📋 Step 5/5 — Generating final report…")

                # After streaming, get the final state
                final_state = graph.get_state(config)
                values      = final_state.values

                if values.get("final_report"):
                    st.session_state.pipeline_result    = values
                    st.session_state.run_complete       = True
                    st.session_state.escalation_pending = False
                    status.update(label="Pipeline complete", state="complete", expanded=False)
                else:
                    # Graph paused at interrupt (escalation)
                    current_state = graph.get_state(config)
                    interrupt_val = None
                    if current_state.tasks:
                        for task in current_state.tasks:
                            if hasattr(task, "interrupts") and task.interrupts:
                                interrupt_val = task.interrupts[0].value
                                break

                    st.session_state.pipeline_result    = values
                    st.session_state.escalation_pending = True
                    st.session_state.escalation_packet  = interrupt_val
                    status.update(label="Pipeline paused — human review required",
                                  state="running", expanded=False)

            except GraphInterrupt:
                current_state = graph.get_state(config)
                interrupt_val = None
                if current_state.tasks:
                    for task in current_state.tasks:
                        if hasattr(task, "interrupts") and task.interrupts:
                            interrupt_val = task.interrupts[0].value
                            break

                st.session_state.pipeline_result    = current_state.values
                st.session_state.escalation_pending = True
                st.session_state.escalation_packet  = interrupt_val
                status.update(label="Pipeline paused — human review required",
                              state="running", expanded=False)

            except Exception as exc:
                status.update(label="Pipeline failed", state="error", expanded=False)
                st.error(f"Pipeline error: {exc}")

    # ── Render results whenever we have state ───────────────────────
    if st.session_state.pipeline_result:
        state  = st.session_state.pipeline_result
        terms  = state.get("terms")
        sp     = state.get("panel_decision")
        report = state.get("final_report")
        results = state.get("specialist_results", [])
        by_type = {r["specialist"]: r["findings"] for r in results}

        comp = by_type.get("compliance", {})
        fin  = by_type.get("financial",  {})
        risk = by_type.get("risk",       {})

        st.divider()

        # ── a) DEAL SUMMARY ─────────────────────────────────────────
        st.subheader("📋 Deal Summary")

        if terms:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Amount",       f"${terms.amount_usd:,.0f}")
            c2.metric("LTV",          f"{terms.ltv:.1%}")
            c3.metric("DTI",          f"{terms.dti:.1%}")
            c4.metric("Credit Score", str(terms.credit_score))
            c5.metric("Rate",         f"{terms.rate_pct}%")

            cb, cj = st.columns(2)
            cb.metric("Borrower",     terms.borrower)
            cj.metric("Jurisdiction", terms.jurisdiction)

        st.divider()

        # ── b) SPECIALIST PANEL ─────────────────────────────────────
        st.subheader("🔬 Specialist Panel")

        pc, pf, pr = st.columns(3)

        # Compliance column
        with pc:
            st.markdown("**⚖️ Compliance**")
            if comp:
                color  = compliance_color(comp.get("status", ""))
                st.markdown(badge(comp.get("status", "—"), color), unsafe_allow_html=True)
                st.markdown("")

                failed = comp.get("failed_rules", [])
                if failed:
                    st.markdown("**Failed rules:**")
                    for r in failed:
                        st.markdown(f"- 🔴 {r}")
                else:
                    st.markdown("No failed rules.")

                notes = comp.get("regulatory_notes", [])
                if notes:
                    st.markdown("**Regulatory notes:**")
                    for n in notes:
                        st.markdown(f"- ⚠️ {n}")

                conf_bar(comp.get("confidence", 0))
            else:
                st.caption("Awaiting results…")

        # Financial column
        with pf:
            st.markdown("**📊 Financial Analyst**")
            if fin:
                color = health_color(fin.get("financial_health", ""))
                st.markdown(badge(fin.get("financial_health", "—"), color), unsafe_allow_html=True)
                st.markdown("")

                strengths = fin.get("strengths", [])
                if strengths:
                    st.markdown("**Strengths:**")
                    for s in strengths:
                        st.markdown(f'<span style="color:#22c55e">✔ {s}</span>', unsafe_allow_html=True)

                concerns = fin.get("concerns", [])
                if concerns:
                    st.markdown("**Concerns:**")
                    for c in concerns:
                        st.markdown(f'<span style="color:#ef4444">✘ {c}</span>', unsafe_allow_html=True)

                conf_bar(fin.get("confidence", 0))
            else:
                st.caption("Awaiting results…")

        # Risk column
        with pr:
            st.markdown("**🎲 Risk Quantifier**")
            if risk:
                composite = risk.get("composite_risk_score", 0)
                level     = risk.get("risk_level", "—")
                st.progress(composite / 100)
                st.caption(f"Composite risk score: {composite:.0f} / 100")

                color = risk_color(level)
                st.markdown(badge(level, color), unsafe_allow_html=True)
                st.markdown("")

                mitigants = risk.get("mitigants", [])
                if mitigants:
                    st.markdown("**Mitigants:**")
                    for m in mitigants:
                        st.markdown(f"- {m}")

                conf_bar(risk.get("confidence", 0))
            else:
                st.caption("Awaiting results…")

        st.divider()

        # ── c) SYNTHESIZER DECISION ─────────────────────────────────
        if sp:
            st.subheader("🔮 Synthesizer Decision")

            with st.container(border=True):
                ai_dec   = sp.decision
                dec_col  = decision_color(ai_dec)

                center_col = st.columns([1, 3, 1])[1]
                with center_col:
                    st.markdown(
                        f'<div style="text-align:center;padding:12px 0;">'
                        f'{badge(ai_dec, dec_col)}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                m1, m2, m3 = st.columns(3)
                m1.metric("Overall Risk Score", f"{sp.overall_risk_score:.0f}/100")
                m2.metric("Panel Confidence",   f"{sp.panel_confidence:.0%}")
                m3.metric("Escalation Required", "Yes" if sp.requires_escalation else "No")

                if sp.key_risks:
                    st.markdown("**Key Risks:**")
                    for kr in sp.key_risks:
                        st.markdown(f"- ⚠️ {kr}")

                if sp.conditions:
                    st.markdown("**Conditions:**")
                    for cond in sp.conditions:
                        st.markdown(f"- 📌 {cond}")

                st.markdown("**Executive Summary:**")
                st.markdown(sp.executive_summary)

            st.divider()

        # ── d) HUMAN REVIEW ─────────────────────────────────────────
        if st.session_state.escalation_pending and not st.session_state.run_complete:
            st.subheader("👤 Human Review Required")
            st.warning("This deal requires human review before a final decision can be issued.")

            packet = st.session_state.escalation_packet
            if packet and isinstance(packet, dict):
                with st.expander("Escalation Packet Details", expanded=True):
                    p1, p2 = st.columns(2)
                    p1.metric("Deal ID",      packet.get("deal_id", "—"))
                    p1.metric("Borrower",     packet.get("borrower", "—"))
                    p2.metric("Amount",       f"${packet.get('amount_usd', 0):,.0f}")
                    p2.metric("AI Decision",  packet.get("ai_decision", "—"))
                    st.metric("Risk Score",   f"{packet.get('risk_score', 0):.0f}/100")
                    st.metric("Confidence",   f"{packet.get('confidence', 0):.0%}")

                    key_risks = packet.get("key_risks", [])
                    if key_risks:
                        st.markdown("**Key Risks:**")
                        for kr in key_risks:
                            st.markdown(f"- ⚠️ {kr}")

                    conditions = packet.get("conditions", [])
                    if conditions:
                        st.markdown("**Conditions:**")
                        for cond in conditions:
                            st.markdown(f"- 📌 {cond}")

                    summary = packet.get("summary", "")
                    if summary:
                        st.markdown("**Summary:**")
                        st.markdown(summary)

            reviewer_notes = st.text_area(
                "Reviewer Notes",
                placeholder="Enter any notes or conditions for your decision…",
                key="reviewer_notes",
            )

            btn_approve, btn_conditional, btn_reject = st.columns(3)
            chosen_decision = None
            if btn_approve.button("✅ Approve", use_container_width=True):
                chosen_decision = "APPROVE"
            if btn_conditional.button("⚠️ Conditional Approve", use_container_width=True):
                chosen_decision = "CONDITIONAL_APPROVE"
            if btn_reject.button("❌ Reject", use_container_width=True):
                chosen_decision = "REJECT"

            if chosen_decision:
                st.session_state["_pending_human_decision"] = chosen_decision

            if "_pending_human_decision" in st.session_state:
                pending = st.session_state["_pending_human_decision"]
                st.info(f"Decision selected: **{pending}** — click Submit to finalise.")

                if st.button("Submit Decision", type="primary", use_container_width=True):
                    graph, _ = build_graph()
                    config   = st.session_state.graph_config

                    with st.status("Resuming pipeline with human decision…", expanded=True) as status:
                        try:
                            st.write(f"Human decision: **{pending}**")
                            final = graph.invoke(
                                Command(resume={"decision": pending,
                                               "notes":    reviewer_notes}),
                                config=config,
                            )
                            st.session_state.pipeline_result    = final
                            st.session_state.run_complete       = True
                            st.session_state.escalation_pending = False
                            del st.session_state["_pending_human_decision"]
                            status.update(label="Pipeline resumed and complete",
                                          state="complete", expanded=False)
                            st.rerun()
                        except Exception as exc:
                            status.update(label="Resume failed", state="error")
                            st.error(f"Resume error: {exc}")

        # ── e) FINAL OUTCOME ────────────────────────────────────────
        if report:
            outcome = report.get("final_outcome", {})
            ai_rec  = report.get("ai_recommendation", {})

            st.subheader("✅ Final Outcome")

            final_dec   = outcome.get("decision", "—")
            final_color = decision_color(final_dec)

            with st.container(border=True):
                st.markdown(
                    f'<div style="text-align:center;padding:16px 0;font-size:1.4rem;">'
                    f'{badge(final_dec, final_color)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                oc1, oc2, oc3 = st.columns(3)
                oc1.metric("Escalated",       "Yes" if outcome.get("escalated") else "No")
                oc2.metric("Human Override",  "Yes" if outcome.get("human_override") else "No")
                oc3.metric("AI Recommended",  ai_rec.get("decision", "—"))

                human_notes = outcome.get("human_notes")
                if human_notes:
                    st.markdown("**Reviewer Notes:**")
                    st.markdown(f"> {human_notes}")

                conds = ai_rec.get("conditions", [])
                if conds:
                    st.markdown("**Conditions Applied:**")
                    for cond in conds:
                        st.markdown(f"- 📌 {cond}")


# ────────────────────────────────────────────────────────────────────
# TAB 2 — AUDIT TRAIL
# ────────────────────────────────────────────────────────────────────

with tab_audit:
    st.subheader("📋 Audit Trail — Decision History")

    if not st.session_state.graph_config:
        st.info("Run a deal first to see the full decision history here.")
    else:
        graph, _ = build_graph()
        config   = st.session_state.graph_config
        deal_id  = st.session_state.deal_id

        try:
            history = list(graph.get_state_history(config))
            if not history:
                st.info("No checkpoints found for this thread.")
            else:
                # ── Header row ───────────────────────────────────────────────
                final_vals = history[0].values  # newest checkpoint = most complete state
                hdr1, hdr2, hdr3, hdr4 = st.columns(4)
                hdr1.metric("Deal ID", deal_id)
                terms = final_vals.get("terms")
                if terms:
                    t = terms if isinstance(terms, dict) else terms.model_dump()
                    hdr2.metric("Borrower", t.get("borrower", "—"))
                    hdr3.metric("Amount", f"${t.get('amount_usd', 0):,.0f}")
                pd = final_vals.get("panel_decision")
                if pd:
                    d = pd if isinstance(pd, dict) else pd.model_dump()
                    DECISION_COLORS = {
                        "APPROVE": "🟢", "CONDITIONAL_APPROVE": "🟡",
                        "REJECT": "🔴", "ESCALATE": "🟠",
                    }
                    icon = DECISION_COLORS.get(d.get("decision", ""), "⚪")
                    hdr4.metric("Final Decision", f"{icon} {d.get('decision', '—')}")

                st.divider()

                # ── Build a node-name → checkpoint map (newest write wins) ──
                # history is newest-first; we want one entry per logical node
                seen_nodes = set()
                ordered_nodes = []
                for snap in reversed(history):  # oldest first
                    meta   = snap.metadata or {}
                    writes = meta.get("writes") or {}
                    node   = next(iter(writes), None) or meta.get("source", "")
                    if node and node not in seen_nodes:
                        seen_nodes.add(node)
                        ordered_nodes.append((node, snap))

                # ── Step-by-step timeline ────────────────────────────────────
                NODE_META = {
                    "__start__":             ("🚀", "Pipeline Started",       "Input received and pipeline initialised."),
                    "start":                 ("🚀", "Pipeline Started",       "Input received and pipeline initialised."),
                    "input":                 ("🚀", "Pipeline Started",       "Input received and pipeline initialised."),
                    "loop":                  ("🔄", "Graph Loop",             "LangGraph internal routing step."),
                    "extract_terms":         ("📄", "Extractor",              "LLM read the PDF and extracted structured loan terms."),
                    "compliance_specialist": ("⚖️",  "Compliance Specialist", "Pure Python if/else checked all terms against policy thresholds."),
                    "financial_analyst":     ("📊", "Financial Analyst",      "LLM assessed the borrower's financial health qualitatively."),
                    "risk_quantifier":       ("🎲", "Risk Quantifier",        "ReAct agent called math tools to calculate credit and concentration risk."),
                    "synthesize":            ("🔮", "Synthesizer",            "LLM aggregated all three specialist reports into a committee decision."),
                    "human_review":          ("👤", "Human Review",           "Pipeline paused for human sign-off on this escalated deal."),
                    "generate_report":       ("📋", "Report Generator",       "Pure Python assembled the final audit report from all accumulated state."),
                }

                for step_i, (node, snap) in enumerate(ordered_nodes):
                    icon, title, desc = NODE_META.get(node, ("⚙️", node, ""))
                    vals = snap.values

                    # pick a border colour based on node type
                    is_last = (step_i == len(ordered_nodes) - 1)
                    expanded = is_last or node in ("extract_terms", "synthesize", "generate_report")

                    ts = getattr(snap, "created_at", None)
                    ts_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts or "")

                    label = f"{icon}  Step {step_i + 1} — {title}   `{ts_str}`"
                    with st.expander(label, expanded=expanded):

                        st.caption(desc)

                        # ── extract_terms ─────────────────────────────────────
                        if node == "extract_terms" and vals.get("terms"):
                            t = vals["terms"]
                            td = t if isinstance(t, dict) else t.model_dump()
                            c1, c2, c3, c4, c5 = st.columns(5)
                            c1.metric("Amount",       f"${td.get('amount_usd', 0):,.0f}")
                            c2.metric("LTV",          f"{td.get('ltv', 0):.1%}")
                            c3.metric("DTI",          f"{td.get('dti', 0):.1%}")
                            c4.metric("Credit Score", td.get('credit_score', '—'))
                            c5.metric("Rate",         f"{td.get('rate_pct', 0)}%")
                            st.markdown(f"**Borrower:** {td.get('borrower')}  |  "
                                        f"**Jurisdiction:** {td.get('jurisdiction')}  |  "
                                        f"**Property:** {td.get('property_type')}  |  "
                                        f"**Purpose:** {td.get('purpose')}  |  "
                                        f"**Term:** {td.get('term_months')} months")

                        # ── specialist results ────────────────────────────────
                        elif node in ("compliance_specialist", "financial_analyst", "risk_quantifier"):
                            results = vals.get("specialist_results", [])
                            specialist_map = {
                                "compliance_specialist": "compliance",
                                "financial_analyst":     "financial",
                                "risk_quantifier":       "risk",
                            }
                            key = specialist_map[node]
                            findings = next((r["findings"] for r in results if r["specialist"] == key), None)
                            conf     = next((r["confidence"] for r in results if r["specialist"] == key), None)

                            if findings:
                                if key == "compliance":
                                    STATUS_COLOR = {"PASS": "🟢", "CONDITIONAL": "🟡", "FAIL": "🔴"}
                                    st.markdown(f"### {STATUS_COLOR.get(findings.get('status',''), '⚪')} {findings.get('status', '—')}")
                                    if conf is not None:
                                        st.progress(conf, text=f"Confidence: {conf:.0%}")
                                    failed = findings.get("failed_rules", [])
                                    notes  = findings.get("regulatory_notes", [])
                                    if failed:
                                        st.error("**Failed Rules:**\n" + "\n".join(f"- {r}" for r in failed))
                                    else:
                                        st.success("All policy thresholds passed.")
                                    if notes:
                                        st.warning("**Regulatory Notes:**\n" + "\n".join(f"- {n}" for n in notes))
                                    st.caption(findings.get("reasoning", ""))

                                elif key == "financial":
                                    HEALTH_COLOR = {"STRONG": "🟢", "ADEQUATE": "🟡", "WEAK": "🟠", "CRITICAL": "🔴"}
                                    h = findings.get("financial_health", "—")
                                    st.markdown(f"### {HEALTH_COLOR.get(h, '⚪')} {h}")
                                    if conf is not None:
                                        st.progress(conf, text=f"Confidence: {conf:.0%}")
                                    ca, cb = st.columns(2)
                                    with ca:
                                        st.markdown("**Strengths**")
                                        for s in findings.get("strengths", []):
                                            st.markdown(f"✅ {s}")
                                    with cb:
                                        st.markdown("**Concerns**")
                                        for c in findings.get("concerns", []):
                                            st.markdown(f"⚠️ {c}")
                                    st.caption(findings.get("reasoning", ""))

                                elif key == "risk":
                                    RISK_COLOR = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}
                                    rl = findings.get("risk_level", "—")
                                    r1, r2, r3 = st.columns(3)
                                    r1.metric("Credit Risk",        f"{findings.get('credit_risk_score', 0):.0f} / 100")
                                    r2.metric("Concentration Risk", f"{findings.get('concentration_risk_score', 0):.0f} / 100")
                                    r3.metric("Composite Score",    f"{findings.get('composite_risk_score', 0):.0f} / 100")
                                    st.markdown(f"### {RISK_COLOR.get(rl, '⚪')} Risk Level: {rl}")
                                    if conf is not None:
                                        st.progress(conf, text=f"Confidence: {conf:.0%}")
                                    mitigants = findings.get("mitigants", [])
                                    if mitigants:
                                        st.markdown("**Mitigating Factors:**")
                                        for m in mitigants:
                                            st.markdown(f"🛡️ {m}")
                                    st.caption(findings.get("reasoning", ""))

                        # ── synthesize ────────────────────────────────────────
                        elif node == "synthesize" and vals.get("panel_decision"):
                            pd = vals["panel_decision"]
                            d  = pd if isinstance(pd, dict) else pd.model_dump()
                            DECISION_BADGE = {
                                "APPROVE":             ("🟢", "green"),
                                "CONDITIONAL_APPROVE": ("🟡", "orange"),
                                "REJECT":              ("🔴", "red"),
                                "ESCALATE":            ("🟠", "orange"),
                            }
                            icon_d, _ = DECISION_BADGE.get(d.get("decision", ""), ("⚪", "gray"))
                            st.markdown(f"## {icon_d} Committee Decision: **{d.get('decision')}**")
                            m1, m2, m3 = st.columns(3)
                            m1.metric("Overall Risk Score", f"{d.get('overall_risk_score', 0):.0f} / 100")
                            m2.metric("Panel Confidence",   f"{d.get('panel_confidence', 0):.0%}")
                            m3.metric("Escalation Required", "Yes" if d.get("requires_escalation") else "No")
                            st.markdown(f"**Executive Summary:** {d.get('executive_summary', '')}")
                            risks = d.get("key_risks", [])
                            conds = d.get("conditions", [])
                            if risks:
                                st.markdown("**Key Risks:**")
                                for r in risks:
                                    st.markdown(f"⚠️ {r}")
                            if conds:
                                st.markdown("**Conditions:**")
                                for c in conds:
                                    st.markdown(f"📌 {c}")

                        # ── human_review ──────────────────────────────────────
                        elif node == "human_review":
                            hd = vals.get("human_decision")
                            hn = vals.get("human_notes")
                            if hd:
                                st.success(f"**Human Decision:** {hd}")
                                if hn:
                                    st.info(f"**Reviewer Notes:** {hn}")
                            else:
                                st.warning("Pipeline paused here waiting for human input.")

                        # ── generate_report ───────────────────────────────────
                        elif node == "generate_report" and vals.get("final_report"):
                            rpt = vals["final_report"]
                            out = rpt.get("final_outcome", {})
                            ai  = rpt.get("ai_recommendation", {})
                            DECISION_BADGE = {
                                "APPROVE":             "🟢",
                                "CONDITIONAL_APPROVE": "🟡",
                                "REJECT":              "🔴",
                                "ESCALATE":            "🟠",
                            }
                            final_dec = out.get("decision", "—")
                            st.markdown(f"## {DECISION_BADGE.get(final_dec, '⚪')} Final Outcome: **{final_dec}**")
                            o1, o2, o3 = st.columns(3)
                            o1.metric("AI Recommended",  ai.get("decision", "—"))
                            o2.metric("Human Override",  "Yes" if out.get("human_override") else "No")
                            o3.metric("Escalated",       "Yes" if out.get("escalated") else "No")
                            if out.get("human_notes"):
                                st.info(f"**Reviewer Notes:** {out['human_notes']}")
                            # Full specialist panel summary
                            st.divider()
                            st.markdown("**Full Specialist Panel**")
                            sp = rpt.get("specialist_panel", {})
                            p1, p2, p3 = st.columns(3)
                            comp = sp.get("compliance") or {}
                            fin  = sp.get("financial")  or {}
                            risk = sp.get("risk")       or {}
                            STATUS_C = {"PASS": "🟢", "CONDITIONAL": "🟡", "FAIL": "🔴"}
                            HEALTH_C = {"STRONG": "🟢", "ADEQUATE": "🟡", "WEAK": "🟠", "CRITICAL": "🔴"}
                            RISK_C   = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}
                            p1.markdown(f"**Compliance:** {STATUS_C.get(comp.get('status',''), '⚪')} {comp.get('status','—')}")
                            p2.markdown(f"**Financial:** {HEALTH_C.get(fin.get('financial_health',''), '⚪')} {fin.get('financial_health','—')}")
                            p3.markdown(f"**Risk:** {RISK_C.get(risk.get('risk_level',''), '⚪')} {risk.get('risk_level','—')} ({risk.get('composite_risk_score',0):.0f}/100)")

                        # ── fallback for loop/start nodes ─────────────────────
                        else:
                            st.markdown(f"*Internal LangGraph routing node — no agent output at this step.*")

        except Exception as exc:
            st.error(f"Could not load audit trail: {exc}")
