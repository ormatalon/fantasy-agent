"use client";

import { ComponentProps, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";

type Status = {
  configured: boolean;
  sleeper_username: string;
  openrouter_model: string;
  has_openrouter_key: boolean;
  has_sleeper_token: boolean;
  league: { league_id: string; name: string } | null;
  last_synced_at: string | null;
  week: string | null;
};
type League = { league_id: string; name: string };
type Message = { role: "user" | "agent"; content: string };

const STARTERS = [
  { label: "Lineup", question: "Who should I start this week?" },
  { label: "Waivers", question: "Any waiver pickups worth a claim?" },
  { label: "Injuries", question: "Any injury news on my roster?" },
  { label: "Results", question: "How did I do last week?" },
];

// Free OpenRouter models checked to call tools correctly, best first. Notes
// from use in this app and OpenRouter's uptime data (2026-10-21).
const MODELS = [
  { id: "nvidia/nemotron-3-ultra-550b-a55b:free", note: "The largest; best at questions that need several steps." },
  { id: "nvidia/nemotron-3-super-120b-a12b:free", note: "The original default: smaller and quick, but often down lately." },
  { id: "nvidia/nemotron-3.5-super-vl:free", note: "Smaller than Ultra, so usually quicker. Less tested here." },
  { id: "minimax/minimax-m3.1-flash:free", note: "Not run by Nvidia, so it works when they are down. Tends to wander on big questions." },
];

async function api<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const res = await fetch(
    path,
    body === undefined
      ? { signal }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? `Request failed (${res.status})`);
  return data as T;
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function ago(iso: string) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 24 * 60) return `${Math.round(min / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
}

const markdownLinks = {
  a: ({ node: _node, ...props }: ComponentProps<"a"> & ExtraProps) => <a {...props} target="_blank" rel="noreferrer" />,
};

function Football({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="football">
      <ellipse cx="32" cy="32" rx="27" ry="17" transform="rotate(-35 32 32)" fill="var(--leather)" />
      <path d="M14 45 Q32 32 50 19" stroke="#fff" strokeWidth="2.5" fill="none" opacity="0.35" />
      <g stroke="#fff" strokeWidth="3" strokeLinecap="round" transform="rotate(-35 32 32)">
        <line x1="22" y1="32" x2="42" y2="32" />
        <line x1="25" y1="28" x2="25" y2="36" />
        <line x1="30" y1="28" x2="30" y2="36" />
        <line x1="35" y1="28" x2="35" y2="36" />
        <line x1="40" y1="28" x2="40" y2="36" />
      </g>
    </svg>
  );
}

export default function Home() {
  const [status, setStatus] = useState<Status | null>(null);
  const [view, setView] = useState<"auto" | "settings" | "league">("auto");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Status>("/api/status").then(setStatus, (e) => setError(errorText(e)));
  }, []);

  const done = (s: Status) => {
    setStatus(s);
    setView("auto");
  };
  const back = () => setView("auto");

  let body;
  if (!status) body = error ? <p className="error">{error}</p> : <p className="hint">Loading...</p>;
  else if (view === "settings" || !status.configured)
    body = <Setup status={status} onSaved={done} onCancel={status.configured ? back : undefined} />;
  else if (view === "league" || !status.league)
    body = (
      <LeaguePicker current={status.league?.league_id} onSynced={done} onCancel={status.league ? back : undefined} />
    );
  else body = <Chat onSynced={setStatus} />;

  return (
    <>
      <header>
        <div className="bar">
          <Football size={34} />
          <h1>Fantasy Agent</h1>
          {status?.configured && (
            <nav>
              {status.league && (
                <button className={`navlink${view === "league" ? " active" : ""}`} onClick={() => setView("league")}>
                  League
                </button>
              )}
              <button className={`navlink${view === "settings" ? " active" : ""}`} onClick={() => setView("settings")}>
                Settings
              </button>
            </nav>
          )}
        </div>
        <div className="strip">
          <div className="bar">
            {status?.league ? (
              <>
                <b>{status.league.name}</b>
                {status.week && <span className="week">Week {status.week}</span>}
                <span title={status.last_synced_at ? new Date(status.last_synced_at).toLocaleString() : undefined}>
                  Synced {status.last_synced_at ? ago(status.last_synced_at) : "never"}
                </span>
              </>
            ) : (
              <span>Your Sleeper fantasy football assistant</span>
            )}
          </div>
        </div>
      </header>
      <main className="app">{body}</main>
      <footer>
        <div className="bar">
          <Football size={18} />
          Fantasy Agent advises. Make lineup changes, adds and waiver claims in the Sleeper app.
        </div>
      </footer>
    </>
  );
}

function Setup({ status, onSaved, onCancel }: { status: Status; onSaved: (s: Status) => void; onCancel?: () => void }) {
  const [username, setUsername] = useState(status.sleeper_username);
  const [key, setKey] = useState("");
  const [token, setToken] = useState("");
  const [model, setModel] = useState(status.openrouter_model);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSaved(
        await api<Status>("/api/settings", {
          sleeper_username: username,
          openrouter_api_key: key,
          sleeper_token: token,
          openrouter_model: model,
        }),
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={save}>
      {!status.configured && <Steps at={1} />}
      <h2>{status.configured ? "Settings" : "Welcome, coach"}</h2>
      {!status.configured && <p className="hint">Two things and you are ready for kickoff.</p>}
      <label>
        Sleeper username
        <small>The name you log in to Sleeper with. Your leagues are found from it.</small>
        <input value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label>
        OpenRouter API key
        <small>
          Lets the agent use an AI model. Create a free key at{" "}
          <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer">
            openrouter.ai/keys
          </a>
          .
        </small>
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={status.has_openrouter_key ? "Saved - leave blank to keep it" : "sk-or-..."}
          required={!status.has_openrouter_key}
        />
      </label>
      <details>
        <summary>Advanced</summary>
        <label>
          Sleeper token (optional)
          <small>
            Only needed to see your pending waiver claims. Log in at sleeper.com, open DevTools, then Application,
            Local Storage, https://sleeper.com, and copy the value of token. It gives full access to your account:
            treat it like a password.
          </small>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={status.has_sleeper_token ? "Saved - leave blank to keep it" : ""}
          />
        </label>
        <label>
          Model
          <small>
            Free models can go down for hours (
            <a href={`https://openrouter.ai/${model}`} target="_blank" rel="noreferrer">
              status
            </a>
            ). If one is not available, pick another:
          </small>
          <input value={model} onChange={(e) => setModel(e.target.value)} />
          <span className="model-picks">
            {MODELS.map((m, i) => (
              <button
                type="button"
                key={m.id}
                className={`model-pick${model === m.id ? " chosen" : ""}`}
                onClick={() => setModel(m.id)}
              >
                <b>
                  {m.id}
                  {i === 0 && <em className="tag">Recommended</em>}
                </b>
                <small>{m.note}</small>
              </button>
            ))}
          </span>
        </label>
      </details>
      {error && <p className="error">{error}</p>}
      <div className="buttons">
        <button className="primary" disabled={busy}>
          {busy ? "Saving..." : "Save"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

// First-run progress: accounts, then a league.
function Steps({ at }: { at: 1 | 2 }) {
  return (
    <ol className="steps">
      {["Your accounts", "Pick a league"].map((label, i) => (
        <li key={label} className={i + 1 < at ? "done" : i + 1 === at ? "now" : ""}>
          <b>{i + 1 < at ? "✓" : i + 1}</b>
          {label}
        </li>
      ))}
    </ol>
  );
}

function LeaguePicker({
  current,
  onSynced,
  onCancel,
}: {
  current?: string;
  onSynced: (s: Status) => void;
  onCancel?: () => void;
}) {
  const [leagues, setLeagues] = useState<League[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<League[]>("/api/leagues").then(
      (found) => {
        setLeagues(found);
        if (found.length) setChosen(found.some((lg) => lg.league_id === current) ? current! : found[0].league_id);
      },
      (e) => setError(errorText(e)),
    );
  }, []);

  async function sync(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSynced(await api<Status>("/api/sync", { league_id: chosen }));
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={sync}>
      {!current && <Steps at={2} />}
      <h2>Pick your league</h2>
      {!leagues && !error && <p className="hint">Looking up your leagues...</p>}
      {leagues && !leagues.length && <p className="hint">No leagues found for this season.</p>}
      {leagues && leagues.length > 0 && (
        <ul className="leagues">
          {leagues.map((lg) => (
            <li key={lg.league_id}>
              <label className={chosen === lg.league_id ? "chosen" : ""}>
                <input
                  type="radio"
                  name="league"
                  checked={chosen === lg.league_id}
                  onChange={() => setChosen(lg.league_id)}
                />
                {lg.name}
              </label>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="error">{error}</p>}
      <div className="buttons">
        <button className="primary" disabled={busy || !chosen}>
          {busy ? "Syncing... the first sync takes a minute" : "Sync this league"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

function Byline() {
  return (
    <span className="byline">
      <Football size={16} />
      Fantasy Agent
    </span>
  );
}

// Anything in progress: a broadcast-style "on the clock" line.
function Ticker({ text }: { text: string }) {
  return (
    <div className="row agent">
      <div className="msg agent typing">
        <span className="clock">On the clock</span>
        <span>{text}</span>
      </div>
    </div>
  );
}

const ASKING = "asking";

const elapsed = (ms: number) => {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function Chat({ onSynced }: { onSynced: (s: Status) => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  // While a question runs: how long it has taken and its last step.
  const [progress, setProgress] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    api<Message[]>("/api/chat")
      .then(setMessages, (e) => setError(errorText(e)))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, error]);

  useEffect(() => {
    if (!busy) input.current?.focus();
  }, [busy]);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await action();
    } catch (e) {
      // A stopped question is not an error.
      if (!(e instanceof DOMException && e.name === "AbortError")) setError(errorText(e));
    } finally {
      setBusy("");
    }
  }

  function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setQuestion("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    const controller = new AbortController();
    request.current = controller;
    const started = Date.now();
    const timer = setInterval(async () => {
      const { step } = await api<{ step: string }>("/api/chat/progress").catch(() => ({ step: "" }));
      setProgress(`${step ? step[0].toUpperCase() + step.slice(1) : "Working"} · ${elapsed(Date.now() - started)}`);
    }, 2000);
    run(ASKING, async () => {
      try {
        const { answer } = await api<{ answer: string }>("/api/chat", { question: q }, controller.signal);
        setMessages((m) => [...m, { role: "agent", content: answer }]);
      } catch (e) {
        // Back in the box, so a failed question is one click from a retry.
        if (!controller.signal.aborted) setQuestion(q);
        throw e;
      } finally {
        clearInterval(timer);
        setProgress("");
        request.current = null;
      }
    });
  }

  function stop() {
    // The server ends the question after its current step; the page stops
    // waiting now.
    api("/api/chat/stop", {}).catch(() => {});
    request.current?.abort();
    setMessages((m) => [...m, { role: "agent", content: "Stopped." }]);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(question);
    }
  }

  const thinking = busy === ASKING;

  return (
    <>
      <div className="actions">
        <h2>Game plan</h2>
        <button
          disabled={!!busy}
          onClick={() => run("Starting a new chat", async () => {
            await api("/api/chat/new", {});
            setMessages([]);
          })}
        >
          New chat
        </button>
        <button
          disabled={!!busy}
          onClick={() => run("Syncing from Sleeper", async () => onSynced(await api<Status>("/api/sync", {})))}
        >
          Sync now
        </button>
      </div>
      <div className="messages">
        {!loaded && <Ticker text="Loading your conversation" />}
        {loaded && !messages.length && !busy && (
          <div className="welcome">
            <div className="hero">
              <Football size={56} />
              <h2>What&apos;s the call this week, coach?</h2>
              <p>Ask about your lineup, waivers, trades, injuries or last week&apos;s results.</p>
            </div>
            <div className="chips">
              {STARTERS.map((s) => (
                <button key={s.label} className="chip" onClick={() => send(s.question)}>
                  <small>{s.label}</small>
                  {s.question}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`row ${m.role}`}>
            {m.role === "agent" ? (
              <article className="msg agent">
                <Byline />
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownLinks}>{m.content}</ReactMarkdown>
              </article>
            ) : (
              <div className="msg user">{m.content}</div>
            )}
          </div>
        ))}
        {busy && <Ticker text={thinking ? progress || "Checking projections, rosters and news" : busy} />}
        {error && <p className="error">{error}</p>}
        <div ref={end} />
      </div>
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          send(question);
        }}
      >
        <textarea
          ref={input}
          aria-label="Your question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKey}
          placeholder="Who should I start at flex?"
          rows={1}
        />
        {thinking ? (
          <button type="button" className="stop" onClick={stop}>
            Stop
          </button>
        ) : (
          <button className="primary" disabled={!!busy || !question.trim()}>
            Ask
          </button>
        )}
      </form>
    </>
  );
}
