import { Module } from '@nestjs/common';
import { DockerfilesController } from './dockerfiles.controller';
import { DockerfilesService } from './dockerfiles.service';
import { QueuesModule } from '../queues/queues.module';

@Module({
  imports: [QueuesModule],
  controllers: [DockerfilesController],
  providers: [DockerfilesService],
  exports: [DockerfilesService],
})
export class DockerfilesModule {}
