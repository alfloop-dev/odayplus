import type { ConstraintClass } from "../types";

/**
 * The eight ODP-FR-NET-002 hard-constraint classes.
 *
 * Duplicated from `shared/governance/netplan_disclosure.py` on purpose. The
 * console cannot ask the server what the classes are before deciding whether a
 * payload disclosed all of them -- that question is exactly what a stale or
 * hostile payload would answer wrongly -- so the console holds the set it
 * checks against. The list is fixed by a requirement, not by a policy version:
 * which classes may be *waived* is registry data and is still read off the
 * payload, but which classes must be *accounted for* is ODP-FR-NET-002 itself.
 */
export const NETPLAN_CONSTRAINT_CLASSES: readonly ConstraintClass[] = [
  "CAPITAL",
  "LEASE",
  "CONSTRUCTION",
  "EQUIPMENT",
  "LABOUR",
  "COVERAGE",
  "DILUTION",
  "SEQUENCING",
];

const KNOWN_CLASSES = new Set<string>(NETPLAN_CONSTRAINT_CLASSES);

/** Why a disclosure could not be read. `null` when it read cleanly. */
export type ConstraintDisclosureDefect =
  | "absent"
  | "malformed"
  | "incomplete"
  | "overlapping"
  | "repeated"
  | "unknown-class";

export type ConstraintDisclosureReading = {
  /** Classes the solve bound. Empty whenever `undeclared` is true. */
  modelled: string[];
  /** Classes the solve left unbound. Empty whenever `undeclared` is true. */
  unmodelled: string[];
  /** True when the payload's ODP-FR-NET-002 standing cannot be determined. */
  undeclared: boolean;
  defect: ConstraintDisclosureDefect | null;
  /** The class names the defect is about, so the operator can act on it. */
  defectClasses: string[];
};

export type ConstraintDisclosureSource =
  | {
      modelledConstraintClasses?: unknown;
      modelled_constraint_classes?: unknown;
      unmodelledConstraintClasses?: unknown;
      unmodelled_constraint_classes?: unknown;
      disclosureUndeclared?: boolean;
    }
  | undefined
  | null;

const UNDECLARED = (
  defect: ConstraintDisclosureDefect,
  defectClasses: string[] = [],
): ConstraintDisclosureReading => ({
  modelled: [],
  unmodelled: [],
  undeclared: true,
  defect,
  defectClasses,
});

function readHalf(raw: unknown): string[] | null {
  if (raw == null) return [];
  if (!Array.isArray(raw)) return null;
  return raw.map((item) => String(item).trim().toUpperCase()).filter((item) => item.length > 0);
}

/**
 * Read a payload's constraint disclosure, or report that it has none.
 *
 * ODP-FR-NET-002 names eight classes and a solve either bound a class or did
 * not, so a truthful disclosure accounts for all eight exactly once. Anything
 * else is unreadable rather than partially readable, and this returns it as
 * `undeclared` with both halves emptied.
 *
 * Emptying them is the point. The console's earlier reading only rejected the
 * payload that named *nothing*, so `modelled=[CAPITAL], unmodelled=[]` came
 * through as a fully modelled plan: one class shown as verified and, worse,
 * "no unmodelled constraints" shown for the seven the payload never mentioned.
 * A subset that the server refuses to believe must not be rendered as a
 * verification claim here either -- an operator reading "已建模: CAPITAL" has
 * been told something about CAPITAL that nothing established.
 *
 * The checks are deliberately the same ones
 * `NetworkRebalanceService._validate_constraint_partition` makes server-side.
 * The console is not the gate -- the submit path refuses independently -- but a
 * console that classified a payload more generously than the gate would draw a
 * live submit button over a plan the server rejects, and the operator would
 * read the refusal as a bug rather than as the finding it is.
 */
export function readConstraintDisclosure(
  source: ConstraintDisclosureSource,
): ConstraintDisclosureReading {
  const rawModelled = readHalf(
    source?.modelledConstraintClasses ?? source?.modelled_constraint_classes,
  );
  const rawUnmodelled = readHalf(
    source?.unmodelledConstraintClasses ?? source?.unmodelled_constraint_classes,
  );
  if (rawModelled === null || rawUnmodelled === null) {
    return UNDECLARED("malformed");
  }

  // The server says so directly when it could not reconcile the row. Honoured
  // first, but never relied upon: an older API, a dropped field or a surface
  // that never classified would send nothing, and the checks below reach the
  // same verdict from the payload alone.
  if (source?.disclosureUndeclared) {
    return UNDECLARED("absent");
  }

  if (rawModelled.length === 0 && rawUnmodelled.length === 0) {
    return UNDECLARED("absent");
  }

  const unknown = [...rawModelled, ...rawUnmodelled].filter((item) => !KNOWN_CLASSES.has(item));
  if (unknown.length > 0) {
    return UNDECLARED("unknown-class", Array.from(new Set(unknown)));
  }

  const repeated = [
    ...rawModelled.filter((item, index) => rawModelled.indexOf(item) !== index),
    ...rawUnmodelled.filter((item, index) => rawUnmodelled.indexOf(item) !== index),
  ];
  if (repeated.length > 0) {
    return UNDECLARED("repeated", Array.from(new Set(repeated)));
  }

  const unmodelledSet = new Set(rawUnmodelled);
  const overlapping = rawModelled.filter((item) => unmodelledSet.has(item));
  if (overlapping.length > 0) {
    return UNDECLARED("overlapping", Array.from(new Set(overlapping)));
  }

  const accountedFor = new Set([...rawModelled, ...rawUnmodelled]);
  const missing = NETPLAN_CONSTRAINT_CLASSES.filter((item) => !accountedFor.has(item));
  if (missing.length > 0) {
    return UNDECLARED("incomplete", [...missing]);
  }

  return {
    modelled: rawModelled,
    unmodelled: rawUnmodelled,
    undeclared: false,
    defect: null,
    defectClasses: [],
  };
}

const DEFECT_LABELS: Record<ConstraintDisclosureDefect, string> = {
  absent: "未列出任何已建模或未建模類別",
  malformed: "揭露欄位格式無法解析",
  incomplete: "未說明下列類別的建模狀態",
  overlapping: "下列類別同時被列為已建模與未建模",
  repeated: "下列類別被重複列出",
  "unknown-class": "出現非 ODP-FR-NET-002 的類別名稱",
};

/** One sentence naming why a disclosure could not be read, for the operator. */
export function describeDisclosureDefect(reading: ConstraintDisclosureReading): string {
  if (!reading.undeclared || reading.defect === null) return "";
  const label = DEFECT_LABELS[reading.defect];
  return reading.defectClasses.length > 0
    ? `${label}：${reading.defectClasses.join(", ")}`
    : label;
}
