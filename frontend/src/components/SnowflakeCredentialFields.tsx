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

const inputClass =
  "mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent";

/**
 * The credential fields, shared by project setup and by adding a second
 * connection later, so the two never drift apart.
 */
export function SnowflakeCredentialFields({
  value,
  onChange,
  disabled,
}: {
  value: SnowflakeCredentials;
  onChange: (next: SnowflakeCredentials) => void;
  disabled?: boolean;
}) {
  const set = <K extends keyof SnowflakeCredentials>(key: K, v: SnowflakeCredentials[K]) =>
    onChange({ ...value, [key]: v });

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="account" className="block text-sm font-medium text-foreground">
          Account identifier
        </label>
        <input
          id="account"
          required
          disabled={disabled}
          value={value.account}
          onChange={(e) => set("account", e.target.value)}
          placeholder="ABCDEFG-HI12345"
          className={`${inputClass} font-mono`}
        />
        <p className="mt-1 text-xs text-zinc-500">
          The part before <span className="font-mono">.snowflakecomputing.com</span>.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="username" className="block text-sm font-medium text-foreground">
            User
          </label>
          <input
            id="username"
            required
            disabled={disabled}
            value={value.username}
            onChange={(e) => set("username", e.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor="authMethod" className="block text-sm font-medium text-foreground">
            Authentication
          </label>
          <select
            id="authMethod"
            disabled={disabled}
            value={value.authMethod}
            onChange={(e) => set("authMethod", e.target.value as SnowflakeCredentials["authMethod"])}
            className={inputClass}
          >
            <option value="SNOWFLAKE">Password</option>
            <option value="SNOWFLAKE_JWT">Key pair</option>
          </select>
        </div>
      </div>

      {value.authMethod === "SNOWFLAKE_JWT" ? (
        <div>
          <label htmlFor="privateKey" className="block text-sm font-medium text-foreground">
            Private key (PEM)
          </label>
          <textarea
            id="privateKey"
            required
            rows={4}
            disabled={disabled}
            value={value.privateKey}
            onChange={(e) => set("privateKey", e.target.value)}
            placeholder="-----BEGIN PRIVATE KEY-----"
            className={`${inputClass} font-mono text-xs`}
          />
        </div>
      ) : (
        <div>
          <label htmlFor="password" className="block text-sm font-medium text-foreground">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            disabled={disabled}
            value={value.password}
            onChange={(e) => set("password", e.target.value)}
            className={inputClass}
          />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="role" className="block text-sm font-medium text-foreground">
            Role <span className="font-normal text-zinc-500">(optional)</span>
          </label>
          <input
            id="role"
            disabled={disabled}
            value={value.role}
            onChange={(e) => set("role", e.target.value)}
            placeholder="ACCOUNTADMIN"
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor="warehouse" className="block text-sm font-medium text-foreground">
            Warehouse <span className="font-normal text-zinc-500">(optional)</span>
          </label>
          <input
            id="warehouse"
            disabled={disabled}
            value={value.warehouse}
            onChange={(e) => set("warehouse", e.target.value)}
            placeholder="COMPUTE_WH"
            className={inputClass}
          />
        </div>
      </div>

      <p className="text-xs text-zinc-500">
        Credentials are encrypted before they are stored and never returned by the API.
      </p>
    </div>
  );
}
