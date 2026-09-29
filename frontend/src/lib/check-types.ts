export type FieldKind = "text" | "number" | "json";

export type FieldDescriptor = {
  key: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  optional?: boolean;
};

export type CheckTypeMeta = {
  value:
    | "ROW_COUNT"
    | "FRESHNESS"
    | "NULL_RATE"
    | "SCHEMA_DRIFT"
    | "CROSS_SOURCE_PARITY"
    | "BRONZE_TO_SILVER_PARITY"
    | "SCD2_INTEGRITY";
  label: string;
  description: string;
  needsSecondaryConnector: boolean;
  fields: FieldDescriptor[];
};

export const CHECK_TYPES: CheckTypeMeta[] = [
  {
    value: "ROW_COUNT",
    label: "Row count",
    description: "Row count on one object, or parity between two objects.",
    needsSecondaryConnector: false,
    fields: [
      { key: "object", label: "Object (fully qualified)", kind: "text", placeholder: "DB.SCHEMA.TABLE" },
      {
        key: "comparisonObject",
        label: "Comparison object (optional)",
        kind: "text",
        placeholder: "DB.SCHEMA.OTHER_TABLE",
        optional: true,
      },
      { key: "minRows", label: "Minimum rows (used if no comparison object)", kind: "number", optional: true },
      { key: "toleranceAbs", label: "Tolerance (absolute row diff)", kind: "number", optional: true },
    ],
  },
  {
    value: "FRESHNESS",
    label: "Freshness",
    description: "Fails if the newest row is older than a threshold.",
    needsSecondaryConnector: false,
    fields: [
      { key: "object", label: "Object (fully qualified)", kind: "text", placeholder: "DB.SCHEMA.TABLE" },
      { key: "timestampColumn", label: "Timestamp column", kind: "text", placeholder: "UPDATED_AT" },
      { key: "maxAgeMinutes", label: "Max age (minutes)", kind: "number" },
    ],
  },
  {
    value: "NULL_RATE",
    label: "Null rate",
    description: "Fails if a column's null ratio exceeds a threshold.",
    needsSecondaryConnector: false,
    fields: [
      { key: "object", label: "Object (fully qualified)", kind: "text", placeholder: "DB.SCHEMA.TABLE" },
      { key: "column", label: "Column", kind: "text" },
      { key: "maxNullRatio", label: "Max null ratio (0-1)", kind: "number" },
    ],
  },
  {
    value: "SCHEMA_DRIFT",
    label: "Schema drift",
    description: "Fails if a table's columns don't match an expected definition.",
    needsSecondaryConnector: false,
    fields: [
      { key: "object", label: "Object (fully qualified)", kind: "text", placeholder: "DB.SCHEMA.TABLE" },
      {
        key: "expectedColumns",
        label: "Expected columns (JSON array of {name, dataType})",
        kind: "json",
        placeholder: '[{"name":"ID","dataType":"NUMBER"}]',
      },
    ],
  },
  {
    value: "CROSS_SOURCE_PARITY",
    label: "Cross-source parity",
    description: "Runs one query per connector and compares a single numeric result.",
    needsSecondaryConnector: true,
    fields: [
      { key: "primaryQuery", label: "Primary query (runs on primary connector)", kind: "text" },
      { key: "secondaryQuery", label: "Secondary query (runs on secondary connector)", kind: "text" },
      { key: "toleranceAbs", label: "Tolerance (absolute)", kind: "number", optional: true },
    ],
  },
  {
    value: "BRONZE_TO_SILVER_PARITY",
    // Named for the bronze → silver case it was written for, but the
    // comparison is layer-agnostic and is generated for silver → gold too.
    // The label says "layer" so a gold check does not read as mislabelled;
    // the stored type string stays put, because renaming it would blank the
    // edit form of every check already saved.
    label: "Layer parity (source → target)",
    description:
      "Proves the target is a deduplicated, lossless projection of the source: one row per composite key, nothing dropped, nothing invented, values intact. Works at any layer — bronze → silver, silver → gold.",
    needsSecondaryConnector: false,
    fields: [
      {
        key: "bronzeObject",
        label: "Source object (fully qualified)",
        kind: "text",
        placeholder: "DB.BRONZE.TABLE_RAW",
      },
      {
        key: "silverObject",
        label: "Target object (fully qualified)",
        kind: "text",
        placeholder: "DB.SILVER.TABLE",
      },
      {
        key: "keyColumns",
        label: "Composite key (JSON array of {name, bronze, silver}) - take this from the MERGE's ON clause",
        kind: "json",
        placeholder: '[{"name":"CUSTOMER_ID","bronze":"RAW_PAYLOAD:customer_id::NUMBER","silver":"CUSTOMER_ID"}]',
      },
      {
        key: "valueColumns",
        label: "Value columns to compare (JSON array of {name, bronze, silver}) - optional but catches mis-mapped fields",
        kind: "json",
        placeholder: '[{"name":"EMAIL","bronze":"RAW_PAYLOAD:email::STRING","silver":"EMAIL"}]',
        optional: true,
      },
      {
        key: "bronzeLoadedAtColumn",
        label: "Source settle-timestamp column (LOADED_AT on bronze, UPDATED_AT on a typed source)",
        kind: "text",
        placeholder: "LOADED_AT",
        optional: true,
      },
      {
        key: "bronzeSequenceColumn",
        label: "Source tiebreaker column (makes latest-per-key deterministic)",
        kind: "text",
        placeholder: "RECORD_ID",
        optional: true,
      },
      {
        key: "sourceFilter",
        label: "Source filter - the MERGE's own WHERE, so both sides describe the same rows",
        kind: "text",
        placeholder: "QUANTITY_ON_HAND > 0",
        optional: true,
      },
      {
        key: "silverFilter",
        label: "Target filter - set this to IS_CURRENT = TRUE when the target is an SCD2 dimension",
        kind: "text",
        placeholder: "IS_CURRENT = TRUE",
        optional: true,
      },
      {
        key: "lagMinutes",
        label: "Settling lag (minutes) - source rows newer than this are still in flight",
        kind: "number",
        optional: true,
      },
      { key: "maxDuplicateKeys", label: "Max duplicate keys allowed in the target", kind: "number", optional: true },
      { key: "maxMissingInSilver", label: "Max source keys missing from the target", kind: "number", optional: true },
      { key: "maxExtraInSilver", label: "Max target keys with no source origin", kind: "number", optional: true },
      { key: "maxValueMismatches", label: "Max value mismatches", kind: "number", optional: true },
    ],
  },
  {
    value: "SCD2_INTEGRITY",
    label: "SCD2 history integrity",
    description:
      "For a type-2 dimension: exactly one current row per key, no overlapping validity windows, no gaps between versions, and every window well-formed. Parity cannot see any of these — a dimension can hold every row it should and still contradict itself.",
    needsSecondaryConnector: false,
    fields: [
      {
        key: "object",
        label: "Dimension table (fully qualified)",
        kind: "text",
        placeholder: "DB.SILVER.MEMBERS",
      },
      {
        key: "naturalKeyColumns",
        label:
          "Natural key (JSON array) - the source's own id, repeated once per version. NOT the surrogate key, which is unique per row and would make every assertion pass without testing anything",
        kind: "json",
        placeholder: '["MEMBER_ID"]',
      },
      { key: "validFromColumn", label: "Valid-from column", kind: "text", placeholder: "VALID_FROM", optional: true },
      { key: "validToColumn", label: "Valid-to column", kind: "text", placeholder: "VALID_TO", optional: true },
      {
        key: "currentFlagColumn",
        label: "Current-row flag column",
        kind: "text",
        placeholder: "IS_CURRENT",
        optional: true,
      },
      {
        key: "openEndedSentinel",
        label:
          "Open-ended marker - the sentinel an open version carries. Leave blank if the table uses NULL instead; the two conventions are not interchangeable and cannot be read off the data",
        kind: "text",
        placeholder: "9999-12-31",
        optional: true,
      },
      {
        key: "maxKeysWithManyCurrent",
        label: "Max keys with more than one current row (leave at 0 - this one doubles every joined measure)",
        kind: "number",
        optional: true,
      },
      { key: "maxKeysWithNoCurrent", label: "Max keys with no current row", kind: "number", optional: true },
      { key: "maxOverlappingVersions", label: "Max overlapping versions", kind: "number", optional: true },
      { key: "maxGappedVersions", label: "Max gaps between versions", kind: "number", optional: true },
      { key: "maxInvalidWindows", label: "Max malformed windows (valid-from at or after valid-to)", kind: "number", optional: true },
    ],
  },
];

export function getCheckTypeMeta(value: string): CheckTypeMeta | undefined {
  return CHECK_TYPES.find((t) => t.value === value);
}
