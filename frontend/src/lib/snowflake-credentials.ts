/**
 * Credential shape and conversion, kept out of the component file so that file
 * exports only a component (fast refresh stops working otherwise).
 */
export type SnowflakeCredentials = {
  account: string;
  username: string;
  authMethod: "SNOWFLAKE" | "SNOWFLAKE_JWT";
  password: string;
  privateKey: string;
  role: string;
  warehouse: string;
};

export const emptyCredentials: SnowflakeCredentials = {
  account: "",
  username: "",
  authMethod: "SNOWFLAKE",
  password: "",
  privateKey: "",
  role: "",
  warehouse: "",
};

export function credentialsToConfig(c: SnowflakeCredentials): Record<string, unknown> {
  return {
    account: c.account.trim(),
    username: c.username.trim(),
    authenticator: c.authMethod,
    role: c.role.trim() || undefined,
    warehouse: c.warehouse.trim() || undefined,
    ...(c.authMethod === "SNOWFLAKE_JWT" ? { privateKey: c.privateKey } : { password: c.password }),
  };
}

export function credentialsComplete(c: SnowflakeCredentials): boolean {
  const secret = c.authMethod === "SNOWFLAKE_JWT" ? c.privateKey : c.password;
  return Boolean(c.account.trim() && c.username.trim() && secret);
}
