import { Module } from '@nestjs/common';
import { EnvironmentsController } from './environments.controller';
import { EnvironmentsService } from './environments.service';
import { PreviewKindController } from './preview-kind.controller';
import { QueuesModule } from '../queues/queues.module';

@Module({
  imports: [QueuesModule],
  controllers: [EnvironmentsController, PreviewKindController],
  providers: [EnvironmentsService],
  exports: [EnvironmentsService],
})
export class EnvironmentsModule {}
