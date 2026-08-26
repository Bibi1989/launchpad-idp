import { Module } from '@nestjs/common';
import { K8sController } from './k8s.controller';
import { K8sService } from './k8s.service';

@Module({
  controllers: [K8sController],
  providers: [K8sService],
  exports: [K8sService],
})
export class K8sModule {}
