import { Module } from '@nestjs/common';

import { CloudProvidersController } from './cloud-providers.controller';
import { CloudProvidersService } from './cloud-providers.service';
import { PluginsModule } from '../plugins/plugins.module';

@Module({
  imports: [PluginsModule],
  controllers: [CloudProvidersController],
  providers: [CloudProvidersService],
})
export class CloudProvidersModule {}
