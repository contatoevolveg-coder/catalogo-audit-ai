from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from typing import List
from backend.app import config
from backend.app.database import get_db, User, Tenant
from backend.app.schemas import UserRegisterRequest, UserResponse, TokenResponse, UserCreateRequest
from backend.app.security.auth import hash_password, verify_password, create_access_token
from backend.app.security.dependencies import get_current_user, get_current_tenant, require_admin
from backend.app.security.rate_limit import get_client_ip, is_locked_out, record_attempt

router = APIRouter(
    prefix="/auth",
    tags=["Autenticação"]
)

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED
)
def register_user(
    request: Request,
    data: UserRegisterRequest,
    x_admin_key: Optional[str] = Header(None, description="Chave de bootstrap administrativa (deve bater com ADMIN_API_KEY)"),
    db: Session = Depends(get_db)
):
    """Cria um novo usuário administrativo no banco de dados.
    
    Protegido por X-Admin-Key de bootstrap e rate limiting.
    """
    ip = get_client_ip(request)

    # 2. Antes de comparar o X-Admin-Key, chame is_locked_out
    if is_locked_out(db, "register", ip, config.LOGIN_MAX_ATTEMPTS, config.LOGIN_LOCKOUT_MINUTES):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de registro. Tente novamente em alguns minutos."
        )

    # Valida chave de bootstrap
    is_valid = config.is_admin_key_configured() and x_admin_key == config.ADMIN_API_KEY
    
    # 3. Depois de comparar, grave record_attempt
    record_attempt(db, "register", ip, ip, success=is_valid)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave administrativa X-Admin-Key inválida ou não configurada."
        )

    # Verifica se já existe um usuário com o mesmo nome
    existing_user = db.query(User).filter(User.username == data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário cadastrado com este username."
        )

    # Cria um novo Tenant para o usuário
    new_tenant = Tenant(name=f"Organização de {data.username}")
    db.add(new_tenant)
    db.commit()
    db.refresh(new_tenant)

    # Cria o novo usuário vinculado ao Tenant
    hashed = hash_password(data.password)
    new_user = User(
        username=data.username,
        hashed_password=hashed,
        role="admin",
        is_active=True,
        tenant_id=new_tenant.id
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post(
    "/login",
    response_model=TokenResponse
)
def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """Realiza o login do usuário autenticando via username e password (OAuth2 standard).
    
    Retorna o Bearer Token JWT.
    """
    ip = get_client_ip(request)
    username = form_data.username

    # 2. ANTES de verificar a senha (evite gastar bcrypt à toa em quem já está bloqueado):
    # chame is_locked_out(db, "login", username, ...) E is_locked_out(db, "login", ip, ...).
    # Se qualquer um dos dois for True, responda HTTPException(429) SEM tocar em verify_password.
    if is_locked_out(db, "login", username, config.LOGIN_MAX_ATTEMPTS, config.LOGIN_LOCKOUT_MINUTES) or \
       is_locked_out(db, "login", ip, config.LOGIN_MAX_ATTEMPTS, config.LOGIN_LOCKOUT_MINUTES):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de login. Tente novamente em alguns minutos."
        )

    # 3. Verifique a senha normalmente.
    user = db.query(User).filter(User.username == username).first()
    success = False
    if user and verify_password(form_data.password, user.hashed_password):
        success = True

    # 4. Depois de verificar (sucesso OU falha), grave DOIS registros via record_attempt
    record_attempt(db, "login", username, ip, success=success)
    record_attempt(db, "login", ip, ip, success=success)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nome de usuário ou senha incorretos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário inativo. Contate o administrador.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Gera e retorna o access token JWT
    access_token = create_access_token(user.username)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer"
    )


@router.get(
    "/me",
    response_model=UserResponse
)
def get_current_user_profile(
    current_user: User = Depends(get_current_user)
):
    """Retorna os dados cadastrais do usuário atualmente autenticado via JWT."""
    return current_user


@router.get(
    "/users",
    response_model=List[UserResponse]
)
def list_tenant_users(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Lista os usuários (operadores/clientes) da mesma organização/tenant."""
    return db.query(User).filter(User.tenant_id == tenant_id).order_by(User.id.asc()).all()


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED
)
def create_tenant_user(
    data: UserCreateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin)
):
    """Cadastra um novo usuário/operador dentro da organização do admin logado.

    Diferente de /auth/register (bootstrap com X-Admin-Key), este endpoint é o
    usado pelo próprio dashboard: um admin cria operadores no seu tenant, sem
    precisar da chave de bootstrap. O novo usuário herda o tenant do admin.
    """
    existing = db.query(User).filter(User.username == data.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário com este username."
        )

    new_user = User(
        username=data.username,
        hashed_password=hash_password(data.password),
        role=data.role,
        is_active=True,
        tenant_id=admin_user.tenant_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_200_OK
)
def delete_tenant_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin)
):
    """Remove um usuário da organização (apenas admins; não pode remover a si mesmo)."""
    if user_id == admin_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Você não pode remover a si mesmo.")

    target = db.query(User).filter(User.id == user_id, User.tenant_id == admin_user.tenant_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado nesta organização.")

    db.delete(target)
    db.commit()
    return {"message": f"Usuário '{target.username}' removido com sucesso."}

