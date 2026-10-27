import { useEffect, useRef, useState } from "react";
import { pendingScoreJob, readCandidateScoreJobs, type CandidateScoreJobsRead } from "./candidateScoreJobs";
import type { NetworkScoringSnapshot } from "./networkScoringTypes";

/** Collection read, masked synchronously when its source/access/location changes. */
export function useCandidateScoreJobs(snapshot: NetworkScoringSnapshot | null, headers: HeadersInit,
  key: string, generation: number, refreshToken: unknown, onTerminal: () => Promise<void>, transport: typeof fetch = fetch) {
  const [value, setValue] = useState<{ input: typeof snapshot; key: string; read: CandidateScoreJobsRead } | null>(null);
  const refreshed = useRef(new Set<string>());
  useEffect(() => {
    if (!snapshot || snapshot.source !== "canonical") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let previousPending = new Set<string>();
    async function load() {
      const result = await readCandidateScoreJobs(snapshot!, headers, controller.signal, transport);
      if (controller.signal.aborted) return;
      setValue({ input: snapshot, key, read: { snapshot: result, status: "ready", generation } });
      const terminal = result.jobs.filter((job) => job.jobId && !pendingScoreJob(job)
        && (previousPending.has(job.jobId) || job.status === "SUCCEEDED" && job.availability === "conflict"));
      const needsRefresh = terminal.some((job) => {
        const identity = `${key}:${job.jobId}:${job.version}`;
        if (refreshed.current.has(identity)) return false;
        refreshed.current.add(identity); return true;
      });
      previousPending = new Set(result.jobs.filter(pendingScoreJob).map((job) => job.jobId!));
      if (needsRefresh) await onTerminal();
      if (!controller.signal.aborted && previousPending.size) timer = setTimeout(load, 1000);
    }
    void load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [snapshot, headers, key, generation, refreshToken, onTerminal, transport]);
  return value?.input === snapshot && value.key === key && value.read.generation === generation
    ? value.read : { snapshot: null, status: "loading", generation } satisfies CandidateScoreJobsRead;
}
