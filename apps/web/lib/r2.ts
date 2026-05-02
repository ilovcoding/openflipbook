import { PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import OSS from "ali-oss";
import {
  readServerEnv,
  requireOSS,
  requireR2,
  resolveBlobStorageProvider,
} from "./env";

let cachedR2Client: S3Client | null = null;
let cachedOSSClient: OSS | null = null;

function r2Client(): { s3: S3Client; bucket: string; publicBaseUrl: string } {
  const env = readServerEnv();
  const r2 = requireR2(env);
  if (!cachedR2Client) {
    cachedR2Client = new S3Client({
      region: "auto",
      endpoint: `https://${r2.accountId}.r2.cloudflarestorage.com`,
      credentials: {
        accessKeyId: r2.accessKeyId,
        secretAccessKey: r2.secretAccessKey,
      },
    });
  }
  return {
    s3: cachedR2Client,
    bucket: r2.bucket,
    publicBaseUrl: r2.publicBaseUrl.replace(/\/$/, ""),
  };
}

function ossClient(): { client: OSS; publicBaseUrl: string } {
  const env = readServerEnv();
  const oss = requireOSS(env);
  if (!cachedOSSClient) {
    cachedOSSClient = new OSS({
      region: oss.region,
      endpoint: oss.endpoint || undefined,
      accessKeyId: oss.accessKeyId,
      accessKeySecret: oss.accessKeySecret,
      bucket: oss.bucket,
      authorizationV4: true,
      secure: true,
    });
  }
  return {
    client: cachedOSSClient,
    publicBaseUrl: oss.publicBaseUrl.replace(/\/$/, ""),
  };
}

export interface UploadedObject {
  key: string;
  url: string;
  contentType: string;
}

export async function uploadJpeg(
  key: string,
  body: Buffer,
  contentType = "image/jpeg"
): Promise<UploadedObject> {
  const env = readServerEnv();
  if (resolveBlobStorageProvider(env) === "oss") {
    const { client, publicBaseUrl } = ossClient();
    await client.put(key, body, {
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": "inline",
        "Cache-Control": "public, max-age=31536000, immutable",
      },
    });
    return { key, url: `${publicBaseUrl}/${key}`, contentType };
  }

  const { s3, bucket, publicBaseUrl } = r2Client();
  await s3.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: key,
      Body: body,
      ContentType: contentType,
      CacheControl: "public, max-age=31536000, immutable",
    })
  );
  return { key, url: `${publicBaseUrl}/${key}`, contentType };
}

export function decodeDataUrl(dataUrl: string): {
  contentType: string;
  bytes: Buffer;
} {
  const match = /^data:([^;]+);base64,(.*)$/i.exec(dataUrl);
  if (!match) throw new Error("not a base64 data URL");
  const contentType = match[1]!;
  const b64 = match[2]!;
  return { contentType, bytes: Buffer.from(b64, "base64") };
}
