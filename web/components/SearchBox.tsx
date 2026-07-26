"use client";

import { useEffect, useRef, useState } from "react";
import { geocode } from "@/lib/api";
import { GeocodeResult } from "@/lib/types";

interface Props {
  placeholder: string;
  value: string;
  onPick: (r: GeocodeResult) => void;
  onTextChange: (text: string) => void;
}

export default function SearchBox({ placeholder, value, onPick, onTextChange }: Props) {
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const skipNext = useRef(false);

  useEffect(() => {
    if (skipNext.current) {
      skipNext.current = false;
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    if (value.trim().length < 3) {
      setResults([]);
      return;
    }
    // 600 ms debounce keeps us polite to the Nominatim proxy
    timer.current = setTimeout(async () => {
      try {
        const rs = await geocode(value.trim());
        setResults(rs);
        setOpen(true);
      } catch {
        setResults([]);
      }
    }, 600);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value]);

  return (
    <div className="searchbox">
      <input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onTextChange(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && results.length > 0 && (
        <ul className="search-results">
          {results.map((r, i) => (
            <li key={i}>
              <button
                type="button"
                onMouseDown={() => {
                  skipNext.current = true;
                  onPick(r);
                  setOpen(false);
                }}
              >
                {r.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
