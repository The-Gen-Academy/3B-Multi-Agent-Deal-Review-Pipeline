# GenAcademy — Project 3B: Multi-Agent Deal Review Pipeline
## Q&A Kit
### Available during the project window

---

> This document is your hint guide. It will not give you the answer. It will help you think through the problem when you are stuck — the same way a senior engineer on your team would.

---

## Project Statement

**Project 3B: Multi-Agent Deal Review Pipeline**

Design and build a multi-agent system that automates the review of a financial deal document. The system must include at least three specialized agents — each with a distinct responsibility — and an orchestration layer that coordinates them into a coherent pipeline.

The project is modeled on real workflows used in loan approvals, insurance underwriting, and investment reviews. The core challenge is not building one smart agent — it is building a system where agents delegate to each other, share state, and produce a structured output that a human could act on.

You are free to choose your own framework, model, and architecture. LangChain/LangGraph is suggested but not required. The deliverable is a working pipeline and a deal review output — not a specific implementation.

**Best suited for:** Financial services roles (JPMC, Wells Fargo, Citi, Morgan Stanley, NYLife), pipeline architects, consultants working on process automation, and program managers focused on workflow design.

---

## Deliverables

| # | Deliverable | What it means |
|---|---|---|
| 1 | Working multi-agent pipeline | At least 3 specialized agents that each do something distinct |
| 2 | Orchestration logic | Something that coordinates agent execution — not just sequential function calls |
| 3 | Sample deal review output | A structured output from a real or synthetic deal document |
| 4 | Architecture explanation | A written or visual description of how your system is designed |

You are not required to build a frontend, deploy to the cloud, or use any specific model. A working Jupyter notebook or Python script with a clear output is sufficient.

---

## Hint 1 — How to Break Down the Problem

Before writing any code, answer these questions on paper.

**About your inputs:**
- What does a deal document actually contain? (borrower name, loan amount, LTV, DTI, credit score, jurisdiction, property type, purpose)
- How will you get structured data out of an unstructured PDF or text blob?
- What happens if a field is missing or ambiguous?

**About your agents:**
- What is each agent's single job? A good agent does one thing well.
- Which agent needs an LLM? Which one could run as pure Python logic?
- What does each agent receive as input, and what does it return?

**About your orchestrator:**
- Does your orchestrator decide the order of execution, or just pass data along?
- What happens when agents disagree? Who resolves it?
- Does any step require a human decision before the pipeline continues?

**About your output:**
- What does a useful deal review look like? What would a loan officer actually need to see?
- How do you represent uncertainty or low confidence?

---

## Hint 2 — Not Every Agent Needs an LLM

Think carefully about what each job actually requires before reaching for a language model.

| Job | Does it need an LLM? | Why |
|---|---|---|
| Extract loan terms from a PDF | Yes | Natural language parsing |
| Check if LTV exceeds 80% | No | This is an if/else check |
| Assess overall financial health qualitatively | Yes | Requires interpretation |
| Calculate a risk score from numbers | Probably not | Math formulas do not need a brain |
| Write a plain-English summary | Yes | Communication task |

Mixing LLM-powered and rule-based agents in the same pipeline is not a compromise — it is good engineering. Use the LLM where language and judgment are required. Use code where logic and math are required.

---

## Hint 3 — Orchestration Is Not Just Calling Functions

The difference between a script and a pipeline is orchestration. Ask yourself:

- Can any of my agents run at the same time, or does each one depend on the previous?
- If agent 2 fails, what happens to the pipeline?
- How does state flow between agents — a shared object, a returned dict, a message?
- If I wanted to add a 4th agent later, how much of my code would I have to rewrite?

Calling `agent1()`, then `agent2(result)`, then `agent3(result)` is a start. But it is not orchestration. Orchestration means the coordination logic is separate from the agents themselves.

---

## Hint 4 — What to Do If You Are Stuck

**"I don't know how to extract terms from a PDF."**
Start with `pypdf` to pull out raw text, then ask an LLM to fill in a structured form. Do not try to regex-parse a PDF.

**"My LLM returns a different format every time."**
Use structured output. In LangChain: `llm.with_structured_output(YourPydanticModel)`. Define the shape you expect and let the framework enforce it.

**"My agents are just three functions calling each other. Is that enough?"**
It depends on what those functions do. The key question: is the orchestration logic — deciding what runs, in what order, and with what data — separate from the agent logic?

**"I don't know what compliance rules to check."**
Make them up. Define 4–6 plausible thresholds: max LTV of 80%, min credit score of 620, max DTI of 43%, max loan of $50M. The point is not real regulatory accuracy — it is demonstrating that the agent checks rules against extracted data.

**"My output looks messy."**
Print a structured report with clear sections: Deal Summary, Compliance Status, Financial Assessment, Risk Score, Final Decision. Even basic formatting makes a large difference in perceived quality.

---

## Common Questions

**Q: Does my deal document have to be real?**
No. A synthetic plain-text or PDF file describing a fictional borrower and loan works fine.

**Q: Can I use OpenAI / Anthropic / Gemini instead of a local model?**
Yes. The project does not require Ollama or any specific model.

**Q: Do I need a frontend or UI?**
No. A notebook or script with printed output is sufficient.

**Q: How many agents is enough?**
Three is the minimum. Each must have a distinct, named responsibility. Three agents that all call the LLM and return raw text do not meet the spirit of the deliverable.

**Q: Does my orchestrator have to be a separate agent?**
Not necessarily. It can be a graph, a controller function, or a coordinator class. What matters is a clear separation between "what decides what runs" and "what does the actual work."

**Q: Can my agents run in parallel?**
Yes, and it is a strong design choice if your agents do not depend on each other's outputs. Compliance, financial analysis, and risk scoring can all start from the same extracted terms simultaneously.

**Q: What if my pipeline needs to pause for human input?**
That is a valid and impressive feature. If you are using LangGraph, look into `interrupt()` and `Command(resume=...)`. If not, a simple input prompt works for demonstration purposes.

---

*GenAcademy — Week 3 | Project 3B*
*Solution Kit released after the submission deadline.*
