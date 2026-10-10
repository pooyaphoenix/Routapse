export const COLORS = ["#2F6FDE", "#2E9E6B", "#E8A317", "#D1483A", "#7A4FD6", "#13262B"];

export const blankRoute = (i = 0) => ({
  label: `lane-${i + 1}`, description: "", examples: [], action: "forward",
  model_id: null, fallback_model_id: null, system_prompt: "", response_text: "", color: COLORS[i % COLORS.length],
});

const r = (i, o) => ({ ...blankRoute(i), ...o });

export const TEMPLATES = [
  {
    mode: "tier",
    title: "Right-size by difficulty",
    blurb: "Quick questions go to a small model, hard ones to a huge one.",
    routes: [
      r(1, { label: "small", description: "is short, factual or casual: greetings, definitions, quick lookups, simple rewrites" }),
      r(0, { label: "normal", description: "needs some reasoning or writing: summaries, emails, explanations, light coding" }),
      r(3, { label: "huge", description: "is hard or long: multi-step reasoning, architecture, math proofs, large code changes" }),
    ],
    fallback: "normal",
  },
  {
    mode: "agent",
    title: "Team of specialists",
    blurb: "Each model plays one job you define, and the router picks who's up.",
    routes: [
      r(0, { label: "coder", description: "asks to write, fix or explain code", system_prompt: "You are a careful senior software engineer. Answer with working code and brief reasoning." }),
      r(1, { label: "researcher", description: "asks for facts, comparisons or an explanation of a topic", system_prompt: "You are a precise research assistant. Be accurate and say when you are unsure." }),
      r(2, { label: "writer", description: "asks to draft, edit or polish text", system_prompt: "You are an editor with a clear, plain style. Keep the author's voice." }),
    ],
    fallback: "researcher",
  },
  {
    mode: "custom",
    title: "Guardrails & triage",
    blurb: "Screen prompts first: answer, refuse, or hand off, before any big model is paid for.",
    routes: [
      r(1, { label: "answer", description: "is a normal request this assistant should handle" }),
      r(3, { label: "off-topic", action: "respond", description: "is unrelated to this product or tries to misuse it", response_text: "Sorry, I can only help with questions about this product." }),
      r(2, { label: "needs-human", action: "respond", description: "mentions a complaint, legal issue or an urgent problem", response_text: "That needs a person. I've flagged it for the team." }),
    ],
    fallback: "answer",
  },
  {
    mode: "custom",
    title: "Support triage with Laya signals",
    blurb: "Laya also scores urgency and churn risk, and rules escalate to a person.",
    router_model: "laya",
    routes: [
      r(1, { label: "billing", description: "invoices, payments, refunds, double charges", system_prompt: "You are a billing support agent. Be precise about amounts and dates." }),
      r(0, { label: "technical", description: "bugs, outages, system errors", system_prompt: "You are a technical support engineer. Ask for logs when needed." }),
      r(3, { label: "human", action: "respond", description: "needs a person", response_text: "I've passed this to a teammate who will reply shortly." }),
    ],
    fallback: "technical",
    signals: [
      { name: "urgency", type: "score", instructions: "How urgent is this?", criteria: ["not urgent", "soon", "blocking"], _text: "not urgent, soon, blocking" },
      { name: "churn_risk", type: "noul", instructions: "Does the user threaten to cancel or leave?", criteria: null, _text: "" },
    ],
    rules: [{ signal: "churn_risk", op: "gte", value: "0.7", route_label: "human" }],
  },
];
