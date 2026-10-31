-- =====================================================================
-- 팀 편성 웹 앱 (Team Former) DB 스키마
-- Supabase SQL Editor에 전체를 붙여 넣고 Run 하면 됨. 여러 번 실행해도 안전함.
-- 모든 테이블 이름은 tf_ 로 시작함 (6장 실습 테이블과 구분).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 학생과 관리자
--   password_hash: bcrypt 해시만 저장 (원래 비밀번호는 저장하지 않음)
--   main_role: 학생은 6개 역할 가운데 하나, 관리자는 NULL
-- ---------------------------------------------------------------------
create table if not exists tf_students (
    id            bigint generated always as identity primary key,
    student_no    text not null unique,
    name          text not null,
    main_role     text check (main_role in
                    ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                     '테스터', '보고서 작성자', '발표자')),
    password_hash text not null,
    is_admin      boolean not null default false,
    constraint tf_students_role_required check (is_admin or main_role is not null)
);

-- ---------------------------------------------------------------------
-- 보조 역할 (학생 1명에 0~2행)
-- ---------------------------------------------------------------------
create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text   not null check (role in
                    ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                     '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

-- ---------------------------------------------------------------------
-- 팀 (id = 팀 번호. 팀1의 id는 1)
-- ---------------------------------------------------------------------
create table if not exists tf_teams (
    id   integer primary key check (id >= 1),
    name text    not null unique
);

-- ---------------------------------------------------------------------
-- 팀원
--   (team_id, slot_no) 기본 키: 같은 자리에 두 명이 동시에 들어오면 먼저 도착한 학생만 기록됨
--   slot_no check 1~4         : 다섯 번째 자리를 DB에서 막음
--   student_id unique         : 한 학생은 한 팀에만 소속됨
-- ---------------------------------------------------------------------
create table if not exists tf_members (
    team_id    integer not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),
    student_id bigint  not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

-- ---------------------------------------------------------------------
-- 지원
--   status: 대기, 승인, 거절
--   reason: 거절 이유 (팀원의 거부 / 승인 직전 검사: ... / 모집 마감)
-- ---------------------------------------------------------------------
create table if not exists tf_applications (
    id          bigint generated always as identity primary key,
    team_id     integer not null references tf_teams(id) on delete cascade,
    student_id  bigint  not null references tf_students(id) on delete cascade,
    status      text    not null default '대기' check (status in ('대기', '승인', '거절')),
    reason      text,
    created_at  timestamptz not null default now(),
    decided_at  timestamptz
);

-- 한 학생은 결과를 기다리는 지원을 동시에 1건만 가질 수 있음 (한 번에 한 팀)
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

create index if not exists tf_applications_team_status
    on tf_applications (team_id, status);

-- ---------------------------------------------------------------------
-- 투표
--   (application_id, voter_id) 기본 키: 한 사람은 한 지원에 한 번만 투표 (바꾸기 불가)
-- ---------------------------------------------------------------------
create table if not exists tf_votes (
    application_id bigint not null references tf_applications(id) on delete cascade,
    voter_id       bigint not null references tf_students(id) on delete cascade,
    vote           text   not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- ---------------------------------------------------------------------
-- 설정 (closed: 모집 마감 여부, proposal: 검사를 통과한 배정안 JSON, confirmed: 확정 여부)
-- ---------------------------------------------------------------------
create table if not exists tf_settings (
    key        text primary key,
    value      text,
    updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- AI 호출 기록 (README의 AI 실패 사례 근거)
--   round: 1, 2, 3 = 1~3차 모델, 0 = 앱이 스스로 배정
-- ---------------------------------------------------------------------
create table if not exists tf_ai_logs (
    id          bigint generated always as identity primary key,
    created_at  timestamptz not null default now(),
    round       integer not null,
    model       text    not null,
    outcome     text    not null,          -- 성공, 시간 초과, HTTP 오류, 빈 응답, 형식 오류, 검사 실패
    detail      text,
    elapsed_sec numeric(7, 1)
);

-- ---------------------------------------------------------------------
-- 보안: Supabase 공개 API(anon 키)로는 tf_ 테이블을 읽고 쓸 수 없게 RLS를 켬.
-- 앱은 DATABASE_URL(DB 주인 계정)로 접속하므로 RLS의 영향을 받지 않음.
-- ---------------------------------------------------------------------
alter table tf_students          enable row level security;
alter table tf_student_sub_roles enable row level security;
alter table tf_teams             enable row level security;
alter table tf_members           enable row level security;
alter table tf_applications      enable row level security;
alter table tf_votes             enable row level security;
alter table tf_settings          enable row level security;
alter table tf_ai_logs           enable row level security;
