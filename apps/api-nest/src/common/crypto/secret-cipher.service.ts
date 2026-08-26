import { createHash } from 'node:crypto';

import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import * as fernet from 'fernet';

/**
 * Fernet-compatible encrypt/decrypt, matching the FastAPI app exactly so both backends
 * can read the same encrypted_payload columns.
 *
 * FastAPI derives its Fernet key as base64url(sha256(SECRETS_ENCRYPTION_KEY)) - see
 * apps/api/app/core/secrets.py `_derive_fernet_key`. We derive it identically here.
 */
@Injectable()
export class SecretCipherService {
  private readonly secret: fernet.Secret;

  constructor(config: ConfigService) {
    const keyMaterial = config.get<string>('secretsEncryptionKey')!;
    const digest = createHash('sha256').update(keyMaterial, 'utf8').digest();
    // Match Python's base64.urlsafe_b64encode: standard base64 (with '=' padding),
    // then '+'->'-' and '/'->'_'. Node's 'base64url' drops padding, which the fernet
    // lib rejects, so build it from 'base64' instead.
    const fernetKey = digest.toString('base64').replace(/\+/g, '-').replace(/\//g, '_');
    this.secret = new fernet.Secret(fernetKey);
  }

  encrypt(plaintext: string): string {
    const token = new fernet.Token({ secret: this.secret });
    return token.encode(plaintext);
  }

  decrypt(ciphertext: string): string {
    // ttl: 0 disables expiry (FastAPI stores long-lived secrets).
    const token = new fernet.Token({ secret: this.secret, token: ciphertext, ttl: 0 });
    return token.decode();
  }
}
