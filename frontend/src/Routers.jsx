import { useEffect, useState } from "react";
import { api, slug } from "./api.js";
import { TEMPLATES, COLORS, blankRoute } from "./templates.js";

const MODES = { tier: "Size tiers", agent: "Specialist agents", custom: "Guardrails & custom" };
const newDraft = () => ({ id: "", name: "", mode: "tier", router_model: "laya", routes: [], fallback_label: null, min_confidence: 0, signals: [], rules: [] });

// Signal criteria are edited as text: choice = "label: description" lines, score = comma-separated levels.
const toText = (s) =>
  Array.isArray(s.criteria) ? s.criteria.join(", ")
  : s.criteria ? Object.entries(s.criteria).map(([k, v]) => `${k}: ${v}`).join("\n") : "";
const fromText = (type, text = "") => {
  const t = text.trim();
  if (!t || type === "noul") return null;
  if (type === "choice")
    return Object.fromEntries(t.split("\n").filter((l) => l.includes(":")).map((l) => {
      const i = l.indexOf(":");
      return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
    }));
  return t.split(",").map((x) => x.trim()).filter(Boolean);
};
const hydrate = (d) => ({ ...d, signals: (d.signals || []).map((s) => ({ ...s, _text: toText(s) })), rules: d.rules || [] });

const clean = (d) => ({
  ...d,
  id: d.id || slug(d.name) || "untitled",
  routes: d.routes.map((r) => ({ ...r, label: r.label.trim(), examples: r.examples.filter((x) => x.trim()) })),
  signals: d.signals.filter((s) => s.name.trim()).map(({ _text, ...s }) => ({ ...s, name: s.name.trim(), criteria: fromText(s.type, _text) })),
  rules: d.rules.filter((r) => r.signal && r.route_label && r.value !== ""),
});

const SIGNAL_TYPES = { noul: "Yes/no probability", score: "Score on levels", choice: "Pick one" };
const OPS = { is: "is", gte: "is at least", lte: "is at most" };

function Signals({ draft, setDraft }) {
  const setSig = (i, s) => setDraft({ ...draft, signals: draft.signals.map((x, j) => (j === i ? s : x)) });
  const setRule = (i, r) => setDraft({ ...draft, rules: draft.rules.map((x, j) => (j === i ? r : x)) });
  const names = draft.signals.map((s) => s.name).filter(Boolean);
  return (
    <section className="signals">
      <h2>Signals and rules</h2>
      <p className="hint">Laya and Jev can answer extra questions about each prompt in the same call. Rules turn those answers into routing: the first matching rule wins over the normal choice.</p>
      {draft.signals.map((s, i) => (
        <div className="sig" key={i}>
          <input aria-label="Signal name" placeholder="churn_risk" value={s.name} onChange={(e) => setSig(i, { ...s, name: slug(e.target.value).replace(/-/g, "_") })} />
          <select aria-label="Signal type" value={s.type} onChange={(e) => setSig(i, { ...s, type: e.target.value })}>
            {Object.entries(SIGNAL_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <input aria-label="Question" placeholder="Does the user threaten to cancel?" value={s.instructions} onChange={(e) => setSig(i, { ...s, instructions: e.target.value })} />
          {s.type === "score" && <input aria-label="Levels" placeholder="not urgent, soon, blocking" value={s._text || ""} onChange={(e) => setSig(i, { ...s, _text: e.target.value })} />}
          {s.type === "choice" && <textarea aria-label="Options" rows={2} placeholder={"billing: invoices and refunds\ntechnical: bugs"} value={s._text || ""} onChange={(e) => setSig(i, { ...s, _text: e.target.value })} />}
          <button className="ghost" onClick={() => setDraft({ ...draft, signals: draft.signals.filter((_, j) => j !== i), rules: draft.rules.filter((r) => r.signal !== s.name) })}>Remove</button>
        </div>
      ))}
      <button className="add-lane" onClick={() => setDraft({ ...draft, signals: [...draft.signals, { name: "", type: "noul", instructions: "", criteria: null, _text: "" }] })}>Add a signal</button>

      {draft.signals.length > 0 && (
        <div className="rules">
          {draft.rules.map((r, i) => (
            <div className="rule" key={i}>
              <span>If</span>
              <select value={r.signal} onChange={(e) => setRule(i, { ...r, signal: e.target.value })}>
                <option value="" disabled>signal</option>{names.map((n) => <option key={n}>{n}</option>)}
              </select>
              <select value={r.op} onChange={(e) => setRule(i, { ...r, op: e.target.value })}>
                {Object.entries(OPS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <input value={r.value} placeholder="0.7" onChange={(e) => setRule(i, { ...r, value: e.target.value })} />
              <span>send to</span>
              <select value={r.route_label} onChange={(e) => setRule(i, { ...r, route_label: e.target.value })}>
                <option value="" disabled>lane</option>{draft.routes.map((x) => <option key={x.label}>{x.label}</option>)}
              </select>
              <button className="ghost" onClick={() => setDraft({ ...draft, rules: draft.rules.filter((_, j) => j !== i) })}>Remove</button>
            </div>
          ))}
          <button className="link" onClick={() => setDraft({ ...draft, rules: [...draft.rules, { signal: names[0] || "", op: "gte", value: "0.7", route_label: "" }] })}>Add a rule</button>
        </div>
      )}
    </section>
  );
}

function Lane({ route, mode, models, lit, onChange, onRemove, goConnections }) {
  const set = (k, v) => onChange({ ...route, [k]: v });
  const direct = route.action === "respond";
  return (
    <div className={"lane" + (lit ? " lit" : "")} style={{ "--c": route.color }}>
      <div className="lamp" title={lit ? "Your last test went here" : ""} />
      <div className="lane-body">
        <div className="lane-top">
          <input className="lane-name" aria-label="Lane name" value={route.label} onChange={(e) => set("label", e.target.value)} />
          <div className="swatches">
            {COLORS.map((c) => (
              <button key={c} type="button" aria-label={"Color " + c} className={"sw" + (c === route.color ? " on" : "")}
                style={{ background: c }} onClick={() => set("color", c)} />
            ))}
          </div>
          <button type="button" className="ghost" onClick={onRemove}>Remove lane</button>
        </div>

        <label>Send here when the prompt…
          <textarea rows={2} value={route.description} placeholder="asks for help with code, mentions a bug, pastes an error…"
            onChange={(e) => set("description", e.target.value)} />
        </label>
        <label>Example prompts (one per line, optional)
          <textarea rows={2} value={route.examples.join("\n")} onChange={(e) => set("examples", e.target.value.split("\n"))} />
        </label>

        <div className="dest">
          <div className="seg" role="group" aria-label="What happens">
            <button type="button" className={!direct ? "on" : ""} onClick={() => set("action", "forward")}>Call a model</button>
            <button type="button" className={direct ? "on" : ""} onClick={() => set("action", "respond")}>Reply directly</button>
          </div>
          {!direct && (
            <div className="chips">
              {models.length === 0 && <button type="button" className="link" onClick={goConnections}>Add a model in Connections first</button>}
              {models.map((m) => (
                <button type="button" key={m.id} className={"chip" + (route.model_id === m.id ? " on" : "")}
                  title={m.description} onClick={() => set("model_id", m.id)}>
                  {m.id}
                </button>
              ))}
              {models.length > 0 && !route.model_id && <span className="nudge">Pick a model</span>}
            </div>
          )}
          {!direct && models.length > 1 && (
            <div className="chips" aria-label="Fallback model">
              <span className="nudge">If it fails, try:</span>
              {models.filter((m) => m.id !== route.model_id).map((m) => (
                <button type="button" key={m.id} className={"chip" + (route.fallback_model_id === m.id ? " on" : "")}
                  title={m.description} onClick={() => set("fallback_model_id", route.fallback_model_id === m.id ? null : m.id)}>
                  {m.id}
                </button>
              ))}
            </div>
          )}
        </div>

        {direct ? (
          <label>Reply text
            <textarea rows={2} value={route.response_text} onChange={(e) => set("response_text", e.target.value)} />
          </label>
        ) : (
          <label>{mode === "agent" ? "Agent role and instructions" : "Extra instructions for this model (optional)"}
            <textarea rows={mode === "agent" ? 3 : 2} value={route.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
          </label>
        )}
      </div>
    </div>
  );
}

function Editor({ draft, setDraft, models, isNew, onSave, onDelete, goConnections }) {
  const [prompt, setPrompt] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const setRoute = (i, r) => setDraft({ ...draft, routes: draft.routes.map((x, j) => (j === i ? r : x)) });
  const addLane = () => setDraft({ ...draft, routes: [...draft.routes, blankRoute(draft.routes.length)] });
  const removeLane = (i) => setDraft({ ...draft, routes: draft.routes.filter((_, j) => j !== i) });
  const applyTemplate = (t) =>
    setDraft({ ...draft, mode: t.mode, routes: t.routes, fallback_label: t.fallback, name: draft.name || t.title,
      router_model: t.router_model || draft.router_model, signals: structuredClone(t.signals || []), rules: structuredClone(t.rules || []) });

  const test = async (execute) => {
    setBusy(true); setErr(""); setResult(null);
    try { setResult(await api.post("/test", { router: clean(draft), prompt, execute })); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const lit = result?.decision?.label;

  return (
    <div className="editor">
      <div className="ed-head">
        <input className="title-input" placeholder="Name this router" value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value, id: isNew ? slug(e.target.value) : draft.id })} />
        <div className="seg" role="group" aria-label="Router model">
          {["jev", "laya"].map((m) => (
            <button key={m} className={draft.router_model === m ? "on" : ""} onClick={() => setDraft({ ...draft, router_model: m })}>
              {m === "jev" ? "Jev" : "Laya"}
            </button>
          ))}
        </div>
        <span className="mode-tag">{MODES[draft.mode]}</span>
      </div>
      <p className="hint">API model name: <code>router:{draft.id || slug(draft.name) || "…"}</code></p>

      {draft.routes.length === 0 ? (
        <div className="starters">
          <h2>Pick a starting point</h2>
          <div className="starter-grid">
            {TEMPLATES.map((t) => (
              <button key={t.mode} className="starter" onClick={() => applyTemplate(t)}>
                <b>{t.title}</b>
                <span>{t.blurb}</span>
                <span className="dots">{t.routes.map((r) => <i key={r.label} style={{ background: r.color }} />)}</span>
              </button>
            ))}
          </div>
          <button className="link" onClick={addLane}>or start with one empty lane</button>
        </div>
      ) : (
        <>
          <div className="yard">
            <div className="inlet"><span>Prompt in</span></div>
            <div className="lanes">
              {draft.routes.map((r, i) => (
                <Lane key={i} route={r} mode={draft.mode} models={models} lit={lit === r.label.trim()}
                  onChange={(x) => setRoute(i, x)} onRemove={() => removeLane(i)} goConnections={goConnections} />
              ))}
              <button className="add-lane" onClick={addLane}>Add a lane</button>
            </div>
          </div>

          <div className="fallback">
            <label>When the router is unsure, use
              <select value={draft.fallback_label || ""} onChange={(e) => setDraft({ ...draft, fallback_label: e.target.value || null })}>
                <option value="">first lane</option>
                {draft.routes.map((r) => <option key={r.label}>{r.label}</option>)}
              </select>
            </label>
            <label>Unsure means confidence below {draft.min_confidence.toFixed(2)}
              <input type="range" min="0" max="1" step="0.05" value={draft.min_confidence}
                onChange={(e) => setDraft({ ...draft, min_confidence: Number(e.target.value) })} />
            </label>
          </div>

          <Signals draft={draft} setDraft={setDraft} />

          <div className="tester">
            <input placeholder="Try a prompt to see which lane lights up" value={prompt} onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && prompt && test(false)} />
            <button disabled={!prompt || busy} onClick={() => test(false)}>Route only</button>
            <button disabled={!prompt || busy} onClick={() => test(true)}>Route and run</button>
            {err && <div className="err">{err}</div>}
            {result && (
              <div className="result">
                <p><b style={{ color: "var(--ink)" }}>{result.decision.label}</b> in {result.decision.latency_ms} ms,
                  confidence {result.decision.confidence.toFixed(2)}. <span className="dim">{result.decision.reason}</span></p>
                {Object.keys(result.decision.signals || {}).length > 0 && (
                  <p className="dim">Signals: {Object.entries(result.decision.signals).map(([k, v]) => `${k} = ${typeof v === "number" ? v.toFixed(2) : v}`).join(", ")}
                    {result.decision.laya_model ? ` (Laya model: ${result.decision.laya_model})` : ""}</p>
                )}
                {result.response && (
                  <pre>{result.response.model ? `[${result.response.model}]\n` : ""}{result.response.text}</pre>
                )}
              </div>
            )}
          </div>
        </>
      )}

      <div className="ed-foot">
        <button className="primary" disabled={!draft.name || !draft.routes.length} onClick={() => onSave(clean(draft))}>Save router</button>
        {!isNew && <button className="ghost danger" onClick={onDelete}>Delete router</button>}
      </div>
    </div>
  );
}

export default function Routers({ goConnections }) {
  const [routers, setRouters] = useState([]);
  const [models, setModels] = useState([]);
  const [draft, setDraft] = useState(null);
  const [isNew, setIsNew] = useState(false);
  const [note, setNote] = useState("");

  const load = async () => {
    try {
      const [r, m] = await Promise.all([api.get("/routers"), api.get("/models")]);
      setRouters(r); setModels(m);
    } catch (e) { setNote(e.message); }
  };
  useEffect(() => { load(); }, []);

  const open = (r) => { setDraft(hydrate(structuredClone(r))); setIsNew(false); setNote(""); };
  const create = () => { setDraft(newDraft()); setIsNew(true); setNote(""); };
  const save = async (d) => {
    try { await api.put(`/routers/${d.id}`, d); setDraft(hydrate(d)); setIsNew(false); setNote("Saved"); load(); }
    catch (e) { setNote(e.message); }
  };
  const remove = async () => {
    if (!confirm(`Delete router "${draft.name}"?`)) return;
    await api.del(`/routers/${draft.id}`); setDraft(null); load();
  };

  return (
    <div className="routers">
      <aside className="rlist">
        <button className="primary wide" onClick={create}>New router</button>
        {routers.length === 0 && <p className="empty">No routers yet.</p>}
        {routers.map((r) => (
          <button key={r.id} className={"ritem" + (draft?.id === r.id ? " on" : "")} onClick={() => open(r)}>
            <b>{r.name}</b>
            <span>{r.router_model === "jev" ? "Jev" : "Laya"}, {r.routes.length} lanes</span>
            <span className="dots">{r.routes.map((x) => <i key={x.label} style={{ background: x.color }} />)}</span>
          </button>
        ))}
      </aside>
      <section className="canvas">
        {note && <div className={note === "Saved" ? "ok" : "err"}>{note}</div>}
        {draft ? (
          <Editor draft={draft} setDraft={setDraft} models={models} isNew={isNew} onSave={save} onDelete={remove} goConnections={goConnections} />
        ) : (
          <div className="blank"><h1>Decide who answers.</h1><p>Create a router, then drag nothing: just describe each lane and pick its model.</p></div>
        )}
      </section>
    </div>
  );
}
