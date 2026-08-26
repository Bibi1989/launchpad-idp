import { Module } from '@nestjs/common';

import { ImportsController } from './imports.controller';
import { RepoImportService } from './repo-import.service';
import { GitImporterService } from './services/git-importer.service';
import { ProjectDetectorService } from './services/project-detector.service';
import { GithubAppService } from '../provisioning/github-app.service';
import { InfraScaffoldService } from './services/infra-scaffold.service';

@Module({
  controllers: [ImportsController],
  providers: [RepoImportService, GitImporterService, ProjectDetectorService, GithubAppService, InfraScaffoldService],
  exports: [RepoImportService],
})
export class ImportsModule {}
