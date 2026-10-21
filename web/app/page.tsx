"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Status = {
  configured: boolean;
  sleeper_username: string;
  openrouter_model: string;
  has_openrouter_key: boolean;
  has_sleeper_token: boolean;
  league: { league_id: string; name: string } | null;
  last_synced_at: string | null;
};
type League = { league_id: string; name: string };
type Message = { role: "user" | "agent"; content: string };

const STARTERS = [
  "Who should I start this week?",
  "Any waiver pickups worth a claim?",
  "Any injury news on my roster?",
  "How did I do last week?",
];

// Free OpenRouter models checked to call tools correctly, best first. Notes
// from use in this app and OpenRouter's uptime data (2026-10-21).
const MODELS = [
  { id: "nvidia/nemotron-3-ultra-550b-a55b:free", note: "Recommended. The largest; best at questions that need several steps." },
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

  let body;
  if (!status) body = error ? <p className="error">{error}</p> : <p className="hint">Loading...</p>;
  else if (view === "settings" || !status.configured) body = <Setup status={status} onSaved={done} />;
  else if (view === "league" || !status.league) body = <LeaguePicker onSynced={done} />;
  else body = <Chat username={status.sleeper_username} onSynced={setStatus} />;

  return (
    <>
      <header>
        <div className="bar">
          <Football size={38} />
          <div className="title">
            <h1>Fantasy Agent</h1>
            <span className="meta">
              {status?.league
                ? `${status.league.name} · synced ${status.last_synced_at ? new Date(status.last_synced_at).toLocaleString() : "never"}`
                : "Your Sleeper fantasy football assistant"}
            </span>
          </div>
          {status?.configured && (
            <nav>
              {status.league && <button className="ghost" onClick={() => setView("league")}>Change league</button>}
              <button className="ghost" onClick={() => setView("settings")}>Settings</button>
            </nav>
          )}
        </div>
      </header>
      <main className="app">
        {body}
        <footer>
          Fantasy Agent advises. Make lineup changes, adds and waiver claims in the Sleeper app.
        </footer>
      </main>
    </>
  );
}

function Setup({ status, onSaved }: { status: Status; onSaved: (s: Status) => void }) {
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
          <input value={model} onChange={(e) => setModel(e.target.value)} list="models" />
          <datalist id="models">
            {MODELS.map((m) => (
              <option key={m.id} value={m.id} />
            ))}
          </datalist>
          <span className="model-picks">
            {MODELS.map((m) => (
              <button
                type="button"
                key={m.id}
                className={`model-pick${model === m.id ? " chosen" : ""}`}
                onClick={() => setModel(m.id)}
              >
                <b>{m.id}</b>
                <small>{m.note}</small>
              </button>
            ))}
          </span>
        </label>
      </details>
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy}>
        {busy ? "Saving..." : "Save"}
      </button>
    </form>
  );
}

function LeaguePicker({ onSynced }: { onSynced: (s: Status) => void }) {
  const [leagues, setLeagues] = useState<League[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<League[]>("/api/leagues").then(
      (found) => {
        setLeagues(found);
        if (found.length) setChosen(found[0].league_id);
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
      <button className="primary" disabled={busy || !chosen}>
        {busy ? "Syncing... the first sync takes a minute" : "Sync this league"}
      </button>
    </form>
  );
}

function Avatar({ role, username }: { role: Message["role"]; username: string }) {
  if (role === "agent")
    return (
      <span className="avatar agent">
        <Football size={22} />
      </span>
    );
  return <span className="avatar user">{(username[0] || "?").toUpperCase()}</span>;
}

function Chat({ username, onSynced }: { username: string; onSynced: (s: Status) => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  // One line per minute while a question runs: how long, and the last step.
  const [notes, setNotes] = useState<string[]>([]);
  const end = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    api<Message[]>("/api/chat").then(setMessages, (e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, notes]);

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
      const minutes = Math.round((Date.now() - started) / 60000);
      if (step) setNotes((n) => [...n, `Still working (${minutes} min). Last step: ${step}.`]);
    }, 60000);
    run("Checking projections, rosters and news", async () => {
      try {
        const { answer } = await api<{ answer: string }>("/api/chat", { question: q }, controller.signal);
        setMessages((m) => [...m, { role: "agent", content: answer }]);
      } finally {
        clearInterval(timer);
        setNotes([]);
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

  const thinking = busy.startsWith("Checking");

  return (
    <>
      <nav className="actions">
        <button
          className="ghost dark"
          disabled={!!busy}
          onClick={() => run("Starting a new chat...", async () => {
            await api("/api/chat/new", {});
            setMessages([]);
          })}
        >
          New chat
        </button>
        <button
          className="ghost dark"
          disabled={!!busy}
          onClick={() => run("Syncing from Sleeper...", async () => onSynced(await api<Status>("/api/sync", {})))}
        >
          Sync now
        </button>
      </nav>
      <div className="messages">
        {!messages.length && !busy && (
          <div className="welcome">
            <Football size={72} />
            <h2>What&apos;s the call this week, coach?</h2>
            <p className="hint">Ask about your lineup, waivers, trades, injuries or last week&apos;s results.</p>
            <div className="chips">
              {STARTERS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`row ${m.role}`}>
            <Avatar role={m.role} username={username} />
            <div className={`msg ${m.role}`}>
              {m.role === "agent" ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown> : m.content}
            </div>
          </div>
        ))}
        {thinking && (
          <div className="row agent">
            <Avatar role="agent" username={username} />
            <div className="msg agent typing">
              <span className="dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span className="hint">{busy}</span>
            </div>
          </div>
        )}
        {thinking && notes.length > 0 && (
          <div className="notes">
            {notes.map((n, i) => (
              <p key={i} className="hint">
                {n}
              </p>
            ))}
          </div>
        )}
        {busy && !thinking && <p className="hint">{busy}</p>}
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
