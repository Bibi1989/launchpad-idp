import { Module } from '@nestjs/common';
import { SchedulerService } from './scheduler.service';
import { QueuesModule } from '../queues/queues.module';
import { K8sModule } from '../k8s/k8s.module';
import { NotifierModule } from '../integrations/notifier.module';

@Module({
  imports: [QueuesModule, K8sModule, NotifierModule],
  providers: [SchedulerService],
  exports: [SchedulerService],
})
export class TasksModule {}
