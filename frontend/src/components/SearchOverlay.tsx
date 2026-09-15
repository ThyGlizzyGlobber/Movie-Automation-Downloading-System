import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import "./SearchOverlay.css";
import Icon from "./Icon";
import { readRecentSearches, rememberSearch } from "../lib/recentSearches";

const SEARCH_SUGGESTIONS = [
  "Dune",
  "The Godfather",
  "Inception",
  "Interstellar",
  "Parasite",
  "The Dark Knight",
];
const TYPE_MS = 90;
const DELETE_MS = 45;
const HOLD_MS = 2400;
const BETWEEN_MS = 1000;

// Revolving placeholder: types a suggested title out, holds, deletes it,
// then moves to the next one — a terminal-style typing effect. Only ever
// shows while the field is genuinely empty (a placeholder disappears the
// instant there's real text), so it never interferes with typing/search.
function useSearchPlaceholderTypewriter(active: boolean) {
  const [placeholder, setPlaceholder] = useState("");
  useEffect(() => {
    if (!active) return;
    let phrase = 0;
    let chars = 0;
    let deleting = false;
    let timer: number;
    function tick() {
      const target = `Search "${SEARCH_SUGGESTIONS[phrase]}"…`;
      if (!deleting) {
        chars++;
        setPlaceholder(target.slice(0, chars));
        if (chars === target.length) {
          deleting = true;
          timer = window.setTimeout(tick, HOLD_MS);
          return;
        }
        timer = window.setTimeout(tick, TYPE_MS);
      } else {
        chars--;
        setPlaceholder(target.slice(0, chars));
        if (chars === 0) {
          deleting = false;
          phrase = (phrase + 1) % SEARCH_SUGGESTIONS.length;
          timer = window.setTimeout(tick, BETWEEN_MS);
          return;
        }
        timer = window.setTimeout(tick, DELETE_MS);
      }
    }
    tick();
    return () => window.clearTimeout(timer);
  }, [active]);
  return placeholder;
}

export default function SearchOverlay({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const [value, setValue] = useState("");
  const [recent, setRecent] = useState<string[]>([]);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const placeholder = useSearchPlaceholderTypewriter(isOpen);

  useEffect(() => {
    document.body.classList.toggle("search-open", isOpen);
    if (isOpen) {
      inputRef.current?.focus();
      setRecent(readRecentSearches());
    }
  }, [isOpen]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && isOpen) onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  function submit(query = value) {
    const q = query.trim();
    if (q) {
      rememberSearch(q);
      navigate(`/search/${encodeURIComponent(q)}`);
      setValue("");
      onClose();
    }
  }

  if (!isOpen) return null;

  return (
    <div
      id="searchOverlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="search-overlay-panel">
        <div className="search-overlay-pill">
          <button
            className="search-overlay-icon-btn"
            aria-label="Search"
            onClick={() => submit()}
          >
            <Icon name="search" />
          </button>
          <input
            ref={inputRef}
            type="search"
            enterKeyHint="search"
            aria-label="Search movies and TV shows"
            value={value}
            placeholder={placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          {value && (
            <button
              className="search-clear-btn"
              aria-label="Clear search"
              onClick={() => {
                setValue("");
                inputRef.current?.focus();
              }}
            >
              ✕
            </button>
          )}
          <kbd className="search-overlay-kbd">esc</kbd>
        </div>
        {recent.length > 0 && (
          <div className="search-overlay-recent">
            <span className="search-overlay-recent-label">Recent</span>
            {recent.map((q) => (
              <button
                key={q}
                className="search-overlay-recent-chip"
                onClick={() => submit(q)}
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
