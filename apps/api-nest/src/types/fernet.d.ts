/**
 * Minimal type declaration for the `fernet` package (no bundled types).
 * Covers only the small surface SecretCipherService uses.
 */
declare module 'fernet' {
  export class Secret {
    constructor(key: string);
  }

  export interface TokenOptions {
    secret: Secret;
    token?: string;
    ttl?: number;
    time?: number;
    iv?: number[];
  }

  export class Token {
    constructor(options: TokenOptions);
    encode(message: string): string;
    decode(): string;
  }
}
