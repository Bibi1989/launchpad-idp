import {
  ConflictException,
  Inject,
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { eq } from 'drizzle-orm';
import * as bcrypt from 'bcryptjs';
import { randomUUID } from 'crypto';

import { Database, DRIZZLE } from '../../database/database.module';
import { users, organizations, orgMembers } from '../../database/schema';
import { CurrentUser } from './current-user.interface';
import {
  AuthConfigResponseDto,
  MeResponseDto,
  TokenResponseDto,
  UserLoginDto,
  UserRegisterDto,
} from './dto/auth.dto';
import { OrgsService } from '../../orgs/orgs.service';

const DEV_USER_EMAIL = 'dev@launchpad.local';
const DEV_USER_DISPLAY_NAME = 'Dev User';
const DEV_USER_PASSWORD = 'dev-password-change-me';

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly jwtService: JwtService,
    private readonly configService: ConfigService,
    private readonly orgsService: OrgsService,
  ) {}

  getConfig(): AuthConfigResponseDto {
    const devLoginEnabled = this.configService.get<boolean>('authDevLoginEnabled', true);
    const oidcEnabled = this.configService.get<boolean>('oidcEnabled', false);
    return {
      dev_login_enabled: devLoginEnabled,
      oidc_enabled: oidcEnabled,
      oidc_provider_name: oidcEnabled ? 'OIDC' : null,
    };
  }

  async register(payload: UserRegisterDto): Promise<TokenResponseDto> {
    const [existing] = await this.db
      .select()
      .from(users)
      .where(eq(users.email, payload.email.toLowerCase().trim()));

    if (existing) {
      throw new ConflictException({
        code: 'email_taken',
        message: 'An account with this email already exists',
      });
    }

    const hashedPassword = await bcrypt.hash(payload.password, 10);
    const newUserId = randomUUID();
    const now = new Date();

    const [user] = await this.db
      .insert(users)
      .values({
        id: newUserId,
        email: payload.email.toLowerCase().trim(),
        passwordHash: hashedPassword,
        displayName: payload.displayName.trim(),
        createdAt: now,
        updatedAt: now,
      })
      .returning();

    const memberships = await this.db
      .select({
        org: organizations,
        role: orgMembers.role,
      })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, user.id));

    if (memberships.length > 0) {
      return await this.createTokenResponse(user, memberships[0].org.id, memberships[0].role, false);
    }

    return await this.createTokenResponse(user, undefined, undefined, true);
  }

  async login(payload: UserLoginDto): Promise<TokenResponseDto> {
    const [user] = await this.db
      .select()
      .from(users)
      .where(eq(users.email, payload.email.toLowerCase().trim()));

    if (
      !user ||
      !user.passwordHash ||
      !(await bcrypt.compare(payload.password, user.passwordHash))
    ) {
      throw new UnauthorizedException({
        code: 'invalid_credentials',
        message: 'Invalid email or password',
      });
    }

    const memberships = await this.db
      .select({
        org: organizations,
        role: orgMembers.role,
      })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, user.id));

    if (memberships.length === 0) {
      return await this.createTokenResponse(user, undefined, undefined, true);
    }

    return await this.createTokenResponse(
      user,
      memberships[0].org.id,
      memberships[0].role,
      false,
    );
  }

  async devLogin(): Promise<TokenResponseDto> {
    let [user] = await this.db
      .select()
      .from(users)
      .where(eq(users.email, DEV_USER_EMAIL));

    const now = new Date();

    if (!user) {
      const hashedPassword = await bcrypt.hash(DEV_USER_PASSWORD, 10);
      const newUserId = randomUUID();
      [user] = await this.db
        .insert(users)
        .values({
          id: newUserId,
          email: DEV_USER_EMAIL,
          passwordHash: hashedPassword,
          displayName: DEV_USER_DISPLAY_NAME,
          createdAt: now,
          updatedAt: now,
        })
        .returning();
    }

    const personalOrg = await this.orgsService.ensurePersonalOrg({
      userId: user.id,
      email: user.email,
      displayName: user.displayName || undefined,
    });

    return await this.createTokenResponse(user, personalOrg.id, 'owner', false);
  }

  async getMe(userToken: CurrentUser): Promise<MeResponseDto> {
    const [user] = await this.db
      .select()
      .from(users)
      .where(eq(users.id, userToken.userId));

    if (!user) {
      throw new UnauthorizedException('User not found');
    }

    const memberships = await this.db
      .select({
        org: organizations,
        role: orgMembers.role,
      })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, user.id));

    const activeOrgId = memberships.length > 0 ? memberships[0].org.id : null;

    return {
      user: {
        id: user.id,
        email: user.email,
        display_name: user.displayName || user.email,
        is_active: true,
        is_superuser: false,
        created_at: user.createdAt.toISOString(),
      },
      orgs: memberships.map((m) => ({
        id: m.org.id,
        slug: m.org.slug,
        name: m.org.name,
        role: m.role,
      })),
      active_org_id: activeOrgId,
      needs_org_setup: memberships.length === 0,
    };
  }

  private async createTokenResponse(
    user: any,
    orgId?: string,
    orgRole?: string,
    needsOrgSetup?: boolean,
  ): Promise<TokenResponseDto> {
    const payload = {
      sub: user.id,
      email: user.email,
      iss: 'launchpad-idp',
      org_id: orgId || null,
      org_role: orgRole || null,
    };

    const token = this.jwtService.sign(payload, {
      expiresIn: '24h',
    });

    // Include the full org list + active org so the SPA can route straight to the
    // workspaces home after login (FastAPI parity). Without this the frontend sees
    // orgs=[] and wrongly redirects existing users to the create-organization page.
    const memberships = await this.db
      .select({ org: organizations, role: orgMembers.role })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, user.id));
    const orgs = memberships.map((m) => ({
      id: m.org.id,
      slug: m.org.slug,
      name: m.org.name,
      role: m.role,
    }));
    const activeOrgId = orgId || (orgs.length > 0 ? orgs[0].id : null);

    return {
      access_token: token,
      token_type: 'bearer',
      expires_in: 86400,
      user: {
        id: user.id,
        email: user.email,
        display_name: user.displayName || user.email,
        is_active: true,
        is_superuser: false,
        created_at: user.createdAt.toISOString(),
      },
      org_id: orgId || activeOrgId,
      org_role: orgRole || null,
      orgs,
      active_org_id: activeOrgId,
      needs_org_setup: needsOrgSetup ?? orgs.length === 0,
    };
  }
}
