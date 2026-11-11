"use client";

import { useEffect, useRef, useState } from "react";
import type { CanonicalIntakeDetail } from "@oday-plus/openapi-client";
import type { CanonicalIntakeReadContext } from "./canonicalIntakeReadClient";

export type CanonicalIntakePageLoader<T> = (context: CanonicalIntakeReadContext, parent: CanonicalIntakeDetail,
  cursor: string, signal: AbortSignal, initial: T) => Promise<T>;

/** Presentation lifetime only; all reads retain their original scoped client. */
export function useCanonicalIntakeReadPage<T>(context: CanonicalIntakeReadContext, parent: CanonicalIntakeDetail,
  initial: T | null, loadPage: CanonicalIntakePageLoader<T>) {
  const lifetime = useRef({ context, parent, initial, loadPage, epoch: 0 });
  if (lifetime.current.context !== context || lifetime.current.parent !== parent
    || lifetime.current.initial !== initial || lifetime.current.loadPage !== loadPage) {
    lifetime.current = { context, parent, initial, loadPage, epoch: lifetime.current.epoch + 1 };
  }
  const epoch = lifetime.current.epoch;
  const [selection, setSelection] = useState<{ epoch: number; cursor: string | null }>({ epoch, cursor: null });
  const cursor = selection.epoch === epoch ? selection.cursor : null;
  const [read, setRead] = useState<{ epoch: number; cursor: string | null; page: T | null;
    busy: boolean; error: unknown | null }>({ epoch, cursor: null, page: initial, busy: false, error: null });
  const latest = useRef({ epoch, cursor });
  latest.current = { epoch, cursor };
  useEffect(() => {
    const controller = new AbortController();
    const current = () => !controller.signal.aborted && latest.current.epoch === epoch && latest.current.cursor === cursor;
    if (cursor !== null && initial !== null) {
      setRead({ epoch, cursor, page: null, busy: true, error: null });
      void loadPage(context, parent, cursor, controller.signal, initial).then(
        (page) => { if (current()) setRead({ epoch, cursor, page, busy: false, error: null }); },
        (error) => { if (current()) setRead({ epoch, cursor, page: null, busy: false, error }); },
      );
    }
    return () => controller.abort();
  }, [context, parent, initial, loadPage, epoch, cursor]);
  const bound = read.epoch === epoch && read.cursor === cursor;
  return {
    page: cursor === null ? initial : bound ? read.page : null,
    busy: cursor !== null && (!bound || read.busy),
    error: cursor !== null && bound ? read.error : null,
    selectCursor: (next: string | null) => setSelection({ epoch, cursor: next }),
  };
}
