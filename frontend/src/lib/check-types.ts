export type FieldKind = "text" | "number" | "json";

export type FieldDescriptor = {
  key: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  optional?: boolean;
};

export type CheckTypeMeta = {
  value: "ROW_COUNT" | "FRESHNESS" | "NULL_RATE" | "SCHEMA_DRIFT" | "CROSS_SOURCE_PARITY";
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
];

export function getCheckTypeMeta(value: string): CheckTypeMeta | undefined {
  return CHECK_TYPES.find((t) => t.value === value);
}
