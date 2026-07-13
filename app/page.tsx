"use client";

import { useState, useRef, useEffect, useCallback, KeyboardEvent } from "react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface Source {
  doc_name: string;
  page_number: number | string;
  category: string;
  section: string;
  hybrid_score: number;
  graph_enriched: boolean;
}

interface Message {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  sources?: Source[];
  chunks_used?: number;
  timestamp: Date;
}

interface DocumentsData {
  [category: string]: string[];
}

// ─── Constants ────────────────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const CATEGORY_COLORS: Record<string, string> = {
  Acts      : "#6366f1",
  Judgments : "#f59e0b",
  POV       : "#10b981",
  "Tax Docs": "#f43f5e",
};

const CATEGORY_EMOJIS: Record<string, string> = {
  Acts      : "📜",
  Judgments : "⚖️",
  POV       : "💡",
  "Tax Docs": "📊",
};

const SUGGESTIONS = [
  { icon: "⚖️", text: "What is gross income under IRC § 61?" },
  { icon: "📜", text: "Explain the deduction limits under § 162 for business expenses." },
  { icon: "💡", text: "What did the court hold regarding tax penalties in recent judgments?" },
  { icon: "📊", text: "Summarize the key provisions of the Tax Cuts and Jobs Act." },
];

// ─── Utility ──────────────────────────────────────────────────────────────────

function genId() {
  return Math.random().toString(36).slice(2, 11);
}

function scoreClass(score: number) {
  if (score >= 0.7) return "score-high";
  if (score >= 0.4) return "score-mid";
  return "score-low";
}

// ─── Source Card Component ────────────────────────────────────────────────────

function SourceCard({ source, index }: { source: Source; index: number }) {
  return (
    <div
      className={`source-card${source.graph_enriched ? " graph-enriched" : ""}`}
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <div className="source-number">{index + 1}</div>
      <div className="source-details">
        <div className="source-name" title={source.doc_name}>
          {source.doc_name}
        </div>
        <div className="source-meta">
          <span className="source-meta-item">
            {CATEGORY_EMOJIS[source.category] || "📄"} {source.category}
          </span>
          {source.page_number && (
            <span className="source-meta-item">• p.{source.page_number}</span>
          )}
          {source.section && (
            <span className="source-meta-item">• {source.section}</span>
          )}
          <span className={`source-score ${scoreClass(source.hybrid_score)}`}>
            {(source.hybrid_score * 100).toFixed(0)}%
          </span>
          {source.graph_enriched && (
            <span className="graph-tag">Graph</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Message Component ────────────────────────────────────────────────────────

function MessageBubble({ message }: { message: Message }) {
  const [sourcesOpen, setSourcesOpen] = useState(true);

  if (message.role === "user") {
    return (
      <div className="message-row user">
        <div className="avatar avatar-user">U</div>
        <div className="message-content">
          <div className="bubble bubble-user">{message.content}</div>
        </div>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div className="message-row">
        <div className="avatar avatar-ai">⚠</div>
        <div className="message-content">
          <div className="error-bubble">
            <span>⚠️</span>
            <span>{message.content}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="message-row">
      <div className="avatar avatar-ai">⚖</div>
      <div className="message-content">
        <div className="bubble bubble-ai">
          {message.content.split("\n").map((line, i) => (
            <p key={i}>{line || "\u00A0"}</p>
          ))}
        </div>

        {message.sources && message.sources.length > 0 && (
          <div className="sources-section">
            <button
              className={`sources-toggle${sourcesOpen ? " open" : ""}`}
              onClick={() => setSourcesOpen(!sourcesOpen)}
              aria-expanded={sourcesOpen}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  d="M2 4L6 8L10 4"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
              <span>
                {message.chunks_used} source{message.chunks_used !== 1 ? "s" : ""} retrieved
              </span>
              {message.sources.some((s) => s.graph_enriched) && (
                <span className="graph-tag">Graph RAG</span>
              )}
            </button>

            {sourcesOpen && (
              <div className="sources-list">
                {message.sources.map((src, i) => (
                  <SourceCard key={i} source={src} index={i} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Thinking Indicator ───────────────────────────────────────────────────────

function ThinkingBubble() {
  return (
    <div className="message-row">
      <div className="avatar avatar-ai">⚖</div>
      <div className="message-content">
        <div className="thinking-bubble">
          <div className="thinking-dot" />
          <div className="thinking-dot" />
          <div className="thinking-dot" />
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function Home() {
  const [messages, setMessages]   = useState<Message[]>([]);
  const [input, setInput]         = useState("");
  const [loading, setLoading]     = useState(false);
  const [useGraph, setUseGraph]   = useState(true);
  const [topK, setTopK]           = useState(5);
  const [apiStatus, setApiStatus] = useState<"online" | "loading" | "error">("loading");
  const [documents, setDocuments] = useState<DocumentsData>({});

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);

  // ── Auto-scroll ─────────────────────────────────────────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // ── Auto-resize textarea ─────────────────────────────────────────────────────
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [input]);

  // ── Health check + document list ─────────────────────────────────────────────
  useEffect(() => {
    const checkHealth = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/health`);
        if (res.ok) {
          setApiStatus("online");
        } else {
          setApiStatus("error");
        }
      } catch {
        setApiStatus("error");
      }
    };

    const loadDocs = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/documents`);
        if (res.ok) {
          const data = await res.json();
          setDocuments(data);
        }
      } catch {
        // silent fail — sidebar just stays empty
      }
    };

    checkHealth();
    loadDocs();
  }, []);

  // ── Send message ─────────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (text: string) => {
    const q = text.trim();
    if (!q || loading) return;

    const userMsg: Message = {
      id        : genId(),
      role      : "user",
      content   : q,
      timestamp : new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/query`, {
        method  : "POST",
        headers : { "Content-Type": "application/json" },
        body    : JSON.stringify({
          question       : q,
          top_k          : topK,
          vector_weight  : 0.6,
          keyword_weight : 0.4,
          use_graph      : useGraph,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      const aiMsg: Message = {
        id          : genId(),
        role        : "assistant",
        content     : data.answer,
        sources     : data.sources,
        chunks_used : data.chunks_used,
        timestamp   : new Date(),
      };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (err: unknown) {
      const errMsg: Message = {
        id        : genId(),
        role      : "error",
        content   : err instanceof Error ? err.message : "An unknown error occurred.",
        timestamp : new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setLoading(false);
    }
  }, [loading, topK, useGraph]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const clearChat = () => setMessages([]);

  // ── Sidebar document count ───────────────────────────────────────────────────
  const totalDocs = Object.values(documents).reduce((sum, arr) => sum + arr.length, 0);

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="app-shell">
      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className="sidebar" aria-label="Document categories">
        <div className="sidebar-logo">
          <div className="logo-icon">⚖️</div>
          <span className="logo-text">LexRAG</span>
          <span className="logo-badge">AI</span>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-label">Document Sources</div>

          {Object.entries(documents).length > 0 ? (
            Object.entries(documents).map(([cat, docs]) => (
              <div key={cat} className="category-card" role="listitem">
                <div
                  className="category-dot"
                  style={{ background: CATEGORY_COLORS[cat] || "#6366f1" }}
                />
                <span className="category-name">
                  {CATEGORY_EMOJIS[cat] || "📄"} {cat}
                </span>
                <span className="category-count">{docs.length}</span>
              </div>
            ))
          ) : (
            <div style={{ padding: "8px", fontSize: "0.78rem", color: "var(--text-muted)" }}>
              {apiStatus === "error"
                ? "⚠ Backend offline"
                : "Loading documents…"}
            </div>
          )}
        </div>

        {totalDocs > 0 && (
          <div
            style={{
              padding: "4px 20px 8px",
              fontSize: "0.7rem",
              color: "var(--text-muted)",
            }}
          >
            {totalDocs} documents indexed
          </div>
        )}

        <div className="sidebar-divider" />

        <div style={{ padding: "8px 12px" }}>
          <div className="sidebar-label">Search Mode</div>
          <div
            style={{
              padding: "8px 12px",
              background: "var(--bg-card)",
              borderRadius: "var(--radius-md)",
              fontSize: "0.76rem",
              color: "var(--text-secondary)",
              lineHeight: "1.6",
            }}
          >
            <div style={{ marginBottom: "4px" }}>
              🔷 Vector (60%) — Gemini embeddings
            </div>
            <div style={{ marginBottom: "4px" }}>
              🔶 BM25 (40%) — rank_bm25
            </div>
            {useGraph && (
              <div>🕸️ Graph RAG — Citation enrichment</div>
            )}
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="status-pill">
            <div className={`status-dot${apiStatus === "loading" ? " loading" : apiStatus === "error" ? " error" : ""}`} />
            <span>
              {apiStatus === "online"
                ? "API online"
                : apiStatus === "error"
                ? "API offline"
                : "Connecting…"}
            </span>
          </div>
        </div>
      </aside>

      {/* ── Main Chat Area ─────────────────────────────────── */}
      <main className="main-area">
        {/* Header */}
        <header className="chat-header">
          <div>
            <div className="header-title">Legal & Tax Research</div>
            <div className="header-subtitle">US Tax Law · Judgments · Acts · POV</div>
          </div>
          <div className="header-spacer" />
          {messages.length > 0 && (
            <button
              className="icon-btn"
              onClick={clearChat}
              title="Clear conversation"
              aria-label="Clear conversation"
            >
              🗑
            </button>
          )}
          <div className="header-badge model-badge">Llama 3.3 70B</div>
          <div className="header-badge">Pinecone + BM25</div>
        </header>

        {/* Messages */}
        <div className="messages-container" role="log" aria-live="polite">
          {messages.length === 0 ? (
            <div className="empty-state" aria-label="Start a conversation">
              <div className="empty-icon">⚖️</div>
              <h1 className="empty-title">Ask LexRAG Anything</h1>
              <p className="empty-subtitle">
                Hybrid AI search across US tax law documents — Acts, Judgments,
                POV papers, and Tax Docs. Powered by Gemini embeddings + BM25.
              </p>
              <div className="suggestion-grid">
                {SUGGESTIONS.map((s, i) => (
                  <button
                    key={i}
                    className="suggestion-card"
                    onClick={() => sendMessage(s.text)}
                    aria-label={`Ask: ${s.text}`}
                  >
                    <span className="suggestion-icon">{s.icon}</span>
                    {s.text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))
          )}

          {loading && <ThinkingBubble />}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="input-area">
          <div className="options-panel">
            <button
              id="toggle-graph"
              className={`option-toggle${useGraph ? " active" : ""}`}
              onClick={() => setUseGraph(!useGraph)}
              aria-pressed={useGraph}
              title="Graph RAG enriches results with citation-linked Acts"
            >
              🕸️ Graph RAG {useGraph ? "On" : "Off"}
            </button>

            <label
              htmlFor="topk-select"
              style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}
            >
              Results:
            </label>
            <select
              id="topk-select"
              className="topk-select"
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              aria-label="Number of results"
            >
              {[3, 5, 8, 10].map((n) => (
                <option key={n} value={n}>Top {n}</option>
              ))}
            </select>
          </div>

          <div className="input-row">
            <textarea
              ref={textareaRef}
              id="chat-input"
              className="chat-textarea"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a legal or tax question… (Enter to send, Shift+Enter for new line)"
              rows={1}
              disabled={loading}
              aria-label="Chat input"
            />
            <div className="input-actions">
              <button
                id="send-button"
                className="send-btn"
                onClick={() => sendMessage(input)}
                disabled={!input.trim() || loading}
                aria-label="Send message"
                title="Send (Enter)"
              >
                {loading ? "⏳" : "↑"}
              </button>
            </div>
          </div>

          <p className="input-hint">
            Hybrid search across 4,691 chunks · Gemini embeddings · BM25 · Graph RAG
          </p>
        </div>
      </main>
    </div>
  );
}
