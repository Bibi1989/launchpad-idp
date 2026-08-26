import { Module } from '@nestjs/common';
import {
  UserCloudCredentialsController,
  UserCredentialsController,
} from './user-credentials.controller';
import { UserCredentialsService } from './user-credentials.service';
import { SecretCipherService } from '../common/crypto/secret-cipher.service';

@Module({
  controllers: [UserCloudCredentialsController, UserCredentialsController],
  providers: [UserCredentialsService, SecretCipherService],
  exports: [UserCredentialsService],
})
export class UserCredentialsModule {}
