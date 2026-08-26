import {
  Body,
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  Post,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';

import { AuthService } from './auth.service';
import { AuthUser } from './current-user.decorator';
import { CurrentUser } from './current-user.interface';
import {
  AuthConfigResponseDto,
  MeResponseDto,
  TokenResponseDto,
  UserLoginDto,
  UserRegisterDto,
} from './dto/auth.dto';
import { JwtAuthGuard } from './jwt-auth.guard';

@ApiTags('auth')
@Controller('auth')
export class AuthController {
  constructor(private readonly authService: AuthService) {}

  @Get('config')
  @ApiOperation({ summary: 'Get authentication features configuration' })
  getConfig(): AuthConfigResponseDto {
    return this.authService.getConfig();
  }

  @Post('register')
  @HttpCode(HttpStatus.CREATED)
  @ApiOperation({ summary: 'Register a new user' })
  register(@Body() payload: UserRegisterDto): Promise<TokenResponseDto> {
    return this.authService.register(payload);
  }

  @Post('login')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Log in with email and password' })
  login(@Body() payload: UserLoginDto): Promise<TokenResponseDto> {
    return this.authService.login(payload);
  }

  @Post('dev-login')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Dev single-click instant login' })
  devLogin(): Promise<TokenResponseDto> {
    return this.authService.devLogin();
  }

  @Get('me')
  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @ApiOperation({ summary: 'Get currently authenticated user details' })
  getMe(@AuthUser() user: CurrentUser): Promise<MeResponseDto> {
    return this.authService.getMe(user);
  }
}
