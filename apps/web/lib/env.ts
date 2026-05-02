export interface ServerEnv {
  MODAL_API_URL: string | null;
  MONGODB_URI: string | null;
  MONGODB_DB: string | null;
  BLOB_STORAGE_PROVIDER: string | null;
  BLOB_STORAGE_PREFIX: string | null;
  R2_ACCOUNT_ID: string | null;
  R2_ACCESS_KEY_ID: string | null;
  R2_SECRET_ACCESS_KEY: string | null;
  R2_BUCKET: string | null;
  R2_PUBLIC_BASE_URL: string | null;
  OSS_REGION: string | null;
  OSS_ENDPOINT: string | null;
  OSS_ACCESS_KEY_ID: string | null;
  OSS_ACCESS_KEY_SECRET: string | null;
  OSS_BUCKET: string | null;
  OSS_PUBLIC_BASE_URL: string | null;
}

export function readServerEnv(): ServerEnv {
  return {
    MODAL_API_URL: process.env.MODAL_API_URL || null,
    MONGODB_URI: process.env.MONGODB_URI || null,
    MONGODB_DB: process.env.MONGODB_DB || null,
    BLOB_STORAGE_PROVIDER: process.env.BLOB_STORAGE_PROVIDER || null,
    BLOB_STORAGE_PREFIX: process.env.BLOB_STORAGE_PREFIX || null,
    R2_ACCOUNT_ID: process.env.R2_ACCOUNT_ID || null,
    R2_ACCESS_KEY_ID: process.env.R2_ACCESS_KEY_ID || null,
    R2_SECRET_ACCESS_KEY: process.env.R2_SECRET_ACCESS_KEY || null,
    R2_BUCKET: process.env.R2_BUCKET || null,
    R2_PUBLIC_BASE_URL: process.env.R2_PUBLIC_BASE_URL || null,
    OSS_REGION: process.env.OSS_REGION || null,
    OSS_ENDPOINT: process.env.OSS_ENDPOINT || null,
    OSS_ACCESS_KEY_ID: process.env.OSS_ACCESS_KEY_ID || null,
    OSS_ACCESS_KEY_SECRET: process.env.OSS_ACCESS_KEY_SECRET || null,
    OSS_BUCKET: process.env.OSS_BUCKET || null,
    OSS_PUBLIC_BASE_URL: process.env.OSS_PUBLIC_BASE_URL || null,
  };
}

export class EnvMissingError extends Error {
  constructor(keys: string[]) {
    super(`Missing required env vars: ${keys.join(", ")}`);
    this.name = "EnvMissingError";
  }
}

export function requireR2(env: ServerEnv) {
  const missing: string[] = [];
  if (!env.R2_ACCOUNT_ID) missing.push("R2_ACCOUNT_ID");
  if (!env.R2_ACCESS_KEY_ID) missing.push("R2_ACCESS_KEY_ID");
  if (!env.R2_SECRET_ACCESS_KEY) missing.push("R2_SECRET_ACCESS_KEY");
  if (!env.R2_BUCKET) missing.push("R2_BUCKET");
  if (!env.R2_PUBLIC_BASE_URL) missing.push("R2_PUBLIC_BASE_URL");
  if (missing.length) throw new EnvMissingError(missing);
  return {
    accountId: env.R2_ACCOUNT_ID!,
    accessKeyId: env.R2_ACCESS_KEY_ID!,
    secretAccessKey: env.R2_SECRET_ACCESS_KEY!,
    bucket: env.R2_BUCKET!,
    publicBaseUrl: env.R2_PUBLIC_BASE_URL!,
  };
}

export function requireOSS(env: ServerEnv) {
  const missing: string[] = [];
  if (!env.OSS_REGION) missing.push("OSS_REGION");
  if (!env.OSS_ACCESS_KEY_ID) missing.push("OSS_ACCESS_KEY_ID");
  if (!env.OSS_ACCESS_KEY_SECRET) missing.push("OSS_ACCESS_KEY_SECRET");
  if (!env.OSS_BUCKET) missing.push("OSS_BUCKET");
  if (!env.OSS_PUBLIC_BASE_URL) missing.push("OSS_PUBLIC_BASE_URL");
  if (missing.length) throw new EnvMissingError(missing);
  return {
    region: env.OSS_REGION!,
    endpoint: env.OSS_ENDPOINT,
    accessKeyId: env.OSS_ACCESS_KEY_ID!,
    accessKeySecret: env.OSS_ACCESS_KEY_SECRET!,
    bucket: env.OSS_BUCKET!,
    publicBaseUrl: env.OSS_PUBLIC_BASE_URL!,
  };
}

export type BlobStorageProvider = "r2" | "oss";

function hasAllR2(env: ServerEnv): boolean {
  return Boolean(
    env.R2_ACCOUNT_ID &&
      env.R2_ACCESS_KEY_ID &&
      env.R2_SECRET_ACCESS_KEY &&
      env.R2_BUCKET &&
      env.R2_PUBLIC_BASE_URL
  );
}

function hasAnyOSS(env: ServerEnv): boolean {
  return Boolean(
    env.OSS_REGION ||
      env.OSS_ENDPOINT ||
      env.OSS_ACCESS_KEY_ID ||
      env.OSS_ACCESS_KEY_SECRET ||
      env.OSS_BUCKET ||
      env.OSS_PUBLIC_BASE_URL
  );
}

function hasAllOSS(env: ServerEnv): boolean {
  return Boolean(
    env.OSS_REGION &&
      env.OSS_ACCESS_KEY_ID &&
      env.OSS_ACCESS_KEY_SECRET &&
      env.OSS_BUCKET &&
      env.OSS_PUBLIC_BASE_URL
  );
}

export function resolveBlobStorageProvider(
  env: ServerEnv
): BlobStorageProvider {
  const requested = env.BLOB_STORAGE_PROVIDER?.toLowerCase();
  if (requested === "oss" || requested === "r2") return requested;
  if (hasAllOSS(env) || (hasAnyOSS(env) && !hasAllR2(env))) return "oss";
  return "r2";
}

export function hasBlobStorage(env: ServerEnv): boolean {
  const provider = resolveBlobStorageProvider(env);
  return provider === "oss" ? hasAllOSS(env) : hasAllR2(env);
}

export function publicBlobUrl(env: ServerEnv, key: string): string | null {
  const provider = resolveBlobStorageProvider(env);
  const base =
    provider === "oss" ? env.OSS_PUBLIC_BASE_URL : env.R2_PUBLIC_BASE_URL;
  if (!base) return null;
  return `${base.replace(/\/$/, "")}/${key}`;
}

export function blobStorageObjectKey(env: ServerEnv, key: string): string {
  const cleanKey = key.replace(/^\/+/, "");
  const prefix = (env.BLOB_STORAGE_PREFIX || "")
    .trim()
    .replace(/^\/+/, "")
    .replace(/\/+$/, "");
  return prefix ? `${prefix}/${cleanKey}` : cleanKey;
}

export function requireMongo(env: ServerEnv) {
  const missing: string[] = [];
  if (!env.MONGODB_URI) missing.push("MONGODB_URI");
  if (!env.MONGODB_DB) missing.push("MONGODB_DB");
  if (missing.length) throw new EnvMissingError(missing);
  return { uri: env.MONGODB_URI!, db: env.MONGODB_DB! };
}
