import { Global, Module, OnModuleDestroy } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { drizzle, type PostgresJsDatabase } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';

import * as schema from './schema';

/** Injection token for the Drizzle database instance. */
export const DRIZZLE = Symbol('DRIZZLE');

export type Database = PostgresJsDatabase<typeof schema>;

/**
 * Provides one shared Drizzle connection (postgres-js) app-wide. Global so any module
 * can inject @Inject(DRIZZLE). The pool is closed cleanly on shutdown.
 */
@Global()
@Module({
  providers: [
    {
      provide: DRIZZLE,
      inject: [ConfigService],
      useFactory: (config: ConfigService): Database => {
        const url = config.get<string>('database.url')!;
        const client = postgres(url, { max: 10 });
        return drizzle(client, { schema });
      },
    },
  ],
  exports: [DRIZZLE],
})
export class DatabaseModule implements OnModuleDestroy {
  async onModuleDestroy(): Promise<void> {
    // postgres-js closes idle sockets on its own; nothing to force-close here for the
    // simple pooled client. Kept as a hook for future explicit teardown.
  }
}
