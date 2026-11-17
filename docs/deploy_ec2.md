# EC2 g4dn 배포 가이드

> ⚠️ **쓰지 않을 때는 반드시 인스턴스를 중지하세요.** g4dn.xlarge(서울)는 켜 둔 동안 **시간당 약 $0.647**입니다.
> 24시간 켜 두면 하루 약 $15.5, 한 달 약 $472입니다. 중지해도 디스크(EBS, 월 약 $11)와 고정 IP(공인 IPv4, 월 약 $3.6) 요금은 계속 나갑니다.
> 아래 [비용](#6-비용-켜고-끄기-예산-알림)의 중지 명령, 예산 알림, 유휴 시 자동 중지를 먼저 설정하세요.

확인 기준일: 2026-11-13. 요금과 AMI 이름은 바뀔 수 있으니 실행 전에 다시 확인하세요.

## 구성

```
인터넷 ──443/80──▶ Caddy (TLS) ──▶ 에이전트 서버 :8000 ──▶ Ollama :11434 (GPU, T4 16GB)
                    (컨테이너)        (컨테이너, 외부 비공개)      (컨테이너, 외부 비공개)
```

- 밖에서 보이는 포트는 Caddy의 443(HTTPS)과 80(인증서 발급·리다이렉트)뿐입니다. 8000과 11434는 호스트에도 열지 않습니다.
- 아이 모드는 같은 인스턴스의 Ollama만 씁니다 (`CHILD_ALLOWED_LLM_HOSTS=ollama`).

## 1. 인스턴스 준비

| 항목 | 값 |
| --- | --- |
| 리전 | 서울 `ap-northeast-2` |
| 인스턴스 | `g4dn.xlarge` (vCPU 4, 메모리 16GiB, NVIDIA T4 16GB, 로컬 NVMe 125GB) |
| AMI | **Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04)** (NVIDIA 드라이버 포함, 이름 끝에 날짜) |
| 디스크 | gp3 **120GB** (산정 근거는 아래) |
| 고정 IP | Elastic IP 1개 (중지·시작해도 주소가 바뀌지 않게) |

### GPU 할당량 먼저 확인

새 계정은 GPU 인스턴스 할당량이 0이라 시작이 거부될 수 있습니다.
Service Quotas → EC2 → **Running On-Demand G and VT instances**가 4 이상인지 확인하고, 아니면 4로 증가 요청하세요(승인까지 시간이 걸림).

```bash
aws service-quotas get-service-quota --region ap-northeast-2 \
  --service-code ec2 --quota-code L-DB2E81BA --query 'Quota.Value'
```

### 최신 AMI ID 찾기 (AWS 공식 SSM 파라미터)

```bash
aws ssm get-parameter --region ap-northeast-2 \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id \
  --query "Parameter.Value" --output text
```

### 디스크 용량 산정

| 항목 | 크기 |
| --- | --- |
| AMI 기본(OS, NVIDIA 드라이버, CUDA 등) | 약 50GB (AMI마다 다름, 시작 후 `df -h`로 확인) |
| 후보 모델 4개: qwen3:14b 9.3GB + qwen3.5:9b 약 7GB + gemma4:12b 약 8GB + exaone3.5:7.8b 4.8GB | 약 30GB |
| 노트북 시험용 작은 모델(qwen3:4b 2.5GB 등), 모델 교체 여유 | 약 15GB |
| Docker 이미지(ollama 약 4GB, 서버, caddy), 로그, SQLite | 약 10GB |
| **합계** | **약 105GB → 120GB 제안** |

모델 크기는 Ollama 라이브러리 기준(기본 4bit 양자화)입니다. 인스턴스에 딸린 로컬 NVMe 125GB는 중지하면 지워지므로 모델 저장에 쓰지 않습니다.

## 2. 보안 그룹

| 포트 | 출처 | 용도 |
| --- | --- | --- |
| 22 (SSH) | **내 IP/32만** | 관리 |
| 443 (HTTPS) | **학교 IP, 팀원 IP만** | 교사 앱, temi, 규칙 엔진 → 에이전트 서버 |
| 80 (HTTP) | 0.0.0.0/0 (도메인 인증서를 쓸 때만) | Let's Encrypt 인증서 발급·갱신, HTTPS로 리다이렉트. API는 이 포트로 제공하지 않음 |
| 11434, 8000 | **열지 않음** | Ollama와 서버는 컨테이너 내부 통신만 |

- 80을 막으면 Let's Encrypt가 인증서를 발급·갱신하지 못합니다(검증 서버 IP가 정해져 있지 않음). 공인 IP + 자체 서명 방식(아래 4-B)을 쓸 때는 80도 학교·팀원 IP로 제한하세요.
- 학교 IP가 바뀌면 443 규칙도 바꿔야 합니다.

```bash
REGION=ap-northeast-2
MY_IP=$(curl -s https://checkip.amazonaws.com)/32
SCHOOL_IP=203.0.113.10/32        # 학교 공인 IP로 바꾸기
TEAM_IPS="198.51.100.21/32 198.51.100.22/32"

SG_ID=$(aws ec2 create-security-group --region $REGION --group-name temi-agent-sg \
  --description "temi agent server" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG_ID --protocol tcp --port 22 --cidr $MY_IP
for ip in $SCHOOL_IP $TEAM_IPS; do
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG_ID --protocol tcp --port 443 --cidr $ip
done
aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG_ID --protocol tcp --port 80 --cidr 0.0.0.0/0
```

## 3. 인스턴스 시작

```bash
AMI_ID=$(aws ssm get-parameter --region $REGION \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id \
  --query Parameter.Value --output text)

INSTANCE_ID=$(aws ec2 run-instances --region $REGION \
  --image-id $AMI_ID --instance-type g4dn.xlarge --key-name <내-키페어> \
  --security-group-ids $SG_ID \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=120,VolumeType=gp3,DeleteOnTermination=true}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=temi-agent}]' \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --region $REGION --instance-ids $INSTANCE_ID
```

### 고정 IP (Elastic IP) 연결

인스턴스를 중지했다가 시작하면 자동으로 받은 공인 IP는 바뀝니다. 그러면 DuckDNS 주소, 보안 그룹, 앱 설정이 모두 어긋나므로 Elastic IP를 붙여 주소를 고정합니다.

콘솔: EC2 → 네트워크 및 보안 → 탄력적 IP → **탄력적 IP 주소 할당** → 만든 주소 선택 → 작업 → **탄력적 IP 주소 연결** → 인스턴스 `temi-agent` 선택.

CLI:

```bash
ALLOC_ID=$(aws ec2 allocate-address --region $REGION --domain vpc \
  --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=temi-agent}]' \
  --query AllocationId --output text)
aws ec2 associate-address --region $REGION --instance-id $INSTANCE_ID --allocation-id $ALLOC_ID
EIP=$(aws ec2 describe-addresses --region $REGION --allocation-ids $ALLOC_ID --query 'Addresses[0].PublicIp' --output text)
echo $EIP   # 이 주소를 DuckDNS와 SSH에 씀
```

- 연결하면 자동으로 받은 공인 IP는 반납되고 Elastic IP로 바뀝니다. 중지·시작해도 연결이 유지됩니다.
- **요금**: 공인 IPv4는 사용 중이든 아니든 시간당 $0.005가 붙습니다(Elastic IP 포함, 인스턴스가 중지돼 있으면 '유휴 IP'로 같은 요금). 한 달에 약 $3.6입니다.
- 프로젝트가 끝나면 연결을 끊고 반납해야 요금이 멈춥니다.
  ```bash
  aws ec2 disassociate-address --region $REGION --association-id <연결 ID>
  aws ec2 release-address --region $REGION --allocation-id $ALLOC_ID
  ```

> 루트 장치 이름이 `/dev/sda1`이 아닐 수 있습니다. `aws ec2 describe-images --image-ids $AMI_ID --query 'Images[0].RootDeviceName'`로 확인하세요.

### 접속 후 GPU·Docker 확인

```bash
ssh -i <키>.pem ubuntu@<Elastic IP>
nvidia-smi                       # Tesla T4가 보여야 함
docker --version && docker compose version
docker info | grep -i nvidia     # nvidia 런타임이 보여야 함
docker run --rm --gpus all ubuntu nvidia-smi   # 컨테이너에서 GPU 확인
```

Docker나 NVIDIA Container Toolkit이 없으면 [NVIDIA Container Toolkit 설치 문서](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)대로 설치하세요.

## 4. HTTPS (통신 보안)

교사 앱, temi, 규칙 엔진은 인터넷으로 API 키를 보냅니다. **HTTP로 열면 키와 아이 대화 문장이 그대로 노출되므로 반드시 HTTPS로만 엽니다.**
`docker/compose.ec2.yml`의 Caddy가 `DOMAIN` 값을 보고 인증서를 알아서 처리합니다.

### 4-A. 무료 DDNS(DuckDNS) 도메인 + Let's Encrypt (채택한 방식)

Elastic IP로 주소가 고정되므로 DuckDNS에는 IP를 **한 번만** 등록하면 됩니다(주기적 갱신 프로그램이 필요 없음).
Caddy는 80 포트로 Let's Encrypt 검증(HTTP-01)을 받아 인증서를 발급·자동 갱신하므로, 추가 플러그인 없이 기본 `caddy:2` 이미지로 됩니다.

1. https://www.duckdns.org 에 로그인(GitHub/Google 계정 등)하고 하위 도메인을 만듭니다. 예: `temi-daycare` → `temi-daycare.duckdns.org`
2. 화면 위의 **token**을 복사합니다. 이 토큰은 비밀값입니다. 저장소나 채팅에 올리지 마세요.
3. DuckDNS 주소가 Elastic IP를 가리키게 합니다(노트북이나 EC2 어디서든).
   ```bash
   curl "https://www.duckdns.org/update?domains=temi-daycare&token=<토큰>&ip=$EIP&verbose=true"
   # 첫 줄이 OK이면 성공 (KO면 도메인 이름이나 토큰 확인)
   nslookup temi-daycare.duckdns.org   # Elastic IP가 나오면 됨 (반영까지 1~2분)
   ```
4. 보안 그룹에서 80을 0.0.0.0/0으로, 443을 학교·팀원 IP로 엽니다([2. 보안 그룹](#2-보안-그룹)).
5. 서버를 띄웁니다.
   ```bash
   export DOMAIN=temi-daycare.duckdns.org
   docker compose -f docker/compose.ec2.yml up -d --build
   docker compose -f docker/compose.ec2.yml logs caddy | grep -i "certificate obtained"   # 발급 확인
   curl https://temi-daycare.duckdns.org/health    # 학교·팀원 IP에서
   ```
6. 교사 앱, temi, 규칙 엔진의 서버 주소를 `https://temi-daycare.duckdns.org`로 맞춥니다.

주의:
- 발급이 실패하면 대부분 80 포트가 막혀 있거나 DuckDNS가 아직 옛 IP를 가리키는 경우입니다. `docker compose ... logs caddy`에서 오류를 확인하세요.
- Elastic IP를 반납하고 새로 받았다면 3번을 다시 실행합니다.
- DuckDNS는 무료 개인 서비스라 드물게 장애가 날 수 있습니다. 실제 운영으로 가면 유료 도메인으로 바꾸고 `DOMAIN`만 바꾸면 됩니다(유료 도메인은 DNS A 레코드를 Elastic IP로).

### 4-B. DDNS도 쓸 수 없을 때 (차선책)

| 방법 | `DOMAIN` 값 | 장점 | 주의 |
| --- | --- | --- | --- |
| ① IP 주소 도메인 서비스 | `3-35-10-20.sslip.io` (IP의 점을 하이픈으로) | 도메인을 사지 않아도 Let's Encrypt 정식 인증서 | 외부 DNS 서비스에 의존. 80이 열려 있어야 함. 발급 한도에 걸리면 실패할 수 있음 |
| ② 공인 IP + 자체 서명 | `3.35.10.20` | 외부 서비스 없음. 80·443 모두 학교·팀원 IP로 제한 가능 | 교사 기기, temi, 규칙 엔진 노트북에 Caddy 루트 인증서를 설치해야 함 |

②의 루트 인증서 꺼내기:

```bash
docker compose -f docker/compose.ec2.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

이 파일을 각 기기의 '신뢰할 수 있는 루트 인증서'로 설치하세요(Android는 설정 → 보안 → 인증서 설치). 앱에서 인증서 검증을 끄는 방식은 쓰지 마세요.

> Cloudflare Tunnel 같은 외부 중계(예: trycloudflare.com)도 HTTPS 주소를 주지만, TLS가 중계 서버에서 풀려 아이 대화 문장이 제3자를 거칩니다. 이 프로젝트의 개인정보 원칙상 권하지 않습니다.

## 5. 서버 띄우기

교사·아이 공용 모델은 **qwen3:14b**로 정했습니다(2026-11-17 평가). 비교 평가를 다시 할 때만 다른 후보 모델을 받으세요.

```bash
git clone <저장소 주소> ~/Server && cd ~/Server
cp .env.example .env
nano .env        # 역할별 API 키(TEACHER_APP_API_KEY 등)를 새로 만든 값으로, TEACHER_LLM__MODEL·CHILD_LLM__MODEL=qwen3:14b

export DOMAIN=temi-daycare.duckdns.org             # 4-A에서 만든 DuckDNS 주소 (또는 4-B의 값)
export OLLAMA_PULL_MODELS=qwen3:14b                # 비교 평가 때: qwen3:14b,qwen3.5:9b,gemma4:12b,exaone3.5:7.8b
export GIT_COMMIT=$(git rev-parse --short HEAD)    # 평가 결과 파일에 코드 커밋을 남김
docker compose -f docker/compose.ec2.yml up -d --build

docker compose -f docker/compose.ec2.yml logs -f ollama-init   # 모델 다운로드 진행 (처음 한 번, 수십 분)
docker compose -f docker/compose.ec2.yml ps                    # 모두 healthy / exited(0) 확인
docker compose -f docker/compose.ec2.yml logs server | grep 워밍업   # "LLM 워밍업 완료: qwen3:14b" 확인
curl https://$DOMAIN/health                                     # 학교·팀원 IP에서
```

- 다시 시작해도 모델(`ollama_models`), DB(`server_data`), 인증서(`caddy_data`)는 볼륨에 남습니다.

### 주의사항

- **`.env` 주석은 줄 끝에 쓰지 말고 윗줄에 쓰세요.** `KEY=값  # 설명`처럼 쓰면 docker compose가 `# 설명`까지 값으로 읽는 경우가 있습니다. `.env.example`은 모두 윗줄 주석으로 바꿔 두었습니다.
- **`.env`를 고친 뒤에는 `docker compose ... up -d`로 다시 만드세요.** `docker compose restart`는 `.env`를 다시 읽지 않습니다.
- **results 폴더 권한**: 서버 컨테이너가 시작할 때 `/app/results`(호스트 `~/Server/results`)와 `/data`의 소유자를 컨테이너 사용자(uid 10001)로 맞춥니다. 예전처럼 `sudo chown`을 따로 할 필요는 없습니다. 호스트에서 결과 파일을 지우려면 `sudo rm`을 쓰세요.
- **모델 상주(`OLLAMA_KEEP_ALIVE=-1`)**: 모델을 GPU에서 내리지 않아 첫 요청이 느려지는 일이 없습니다. 대신 GPU 메모리를 계속 차지하므로, 다른 모델을 시험할 때는 `OLLAMA_KEEP_ALIVE=30m docker compose ... up -d`처럼 바꾸거나 `docker compose ... exec ollama ollama stop <모델>`로 내립니다.
- **시작 워밍업**: 서버가 시작하면서 Ollama 모델에 짧은 요청을 보내 GPU에 올립니다(최대 5분, 백그라운드). 인스턴스를 켠 직후 1~2분은 첫 요청이 느릴 수 있으니, 시연 전에 로그에서 "워밍업 완료"를 확인하세요.
- **교사 모드 시간 제한**: 호출 25초, 지시 전체 60초입니다. 워밍업 전에 요청하면 "응답이 늦어지고 있어요"가 나올 수 있습니다.
- **temi 연결**: 실제 temi로 움직이려면 `.env`에 `ROBOT_EXECUTOR=control`을 넣고 `up -d`로 다시 만듭니다. temi 앱은 `wss://$DOMAIN/ws/temi`로 접속합니다([control_protocol.md](control_protocol.md)). Caddy는 WebSocket을 그대로 넘기며, 설정을 다시 읽어도 5분 동안 연결을 유지합니다(`stream_close_delay`). temi 여러 대를 붙이면 `TEMI_DEFAULT_DEVICE_ID`로 교사 지시를 받을 temi를 정하세요.
- **서버 재시작 시 temi 연결이 끊깁니다.** temi 앱이 자동으로 다시 붙어야 하고, 끊긴 순간 진행 중이던 작업은 `disconnected`로 기록됩니다.

## 6. 비용: 켜고 끄기, 예산 알림

### 요금 (서울, 온디맨드, 2026-11 기준 참고값)

| 항목 | 요금 |
| --- | --- |
| g4dn.xlarge | 약 **$0.647/시간** (켜져 있을 때만) |
| EBS gp3 120GB | GB당 월 약 $0.09 → **월 약 $11** (중지해도 나감) |
| 공인 IPv4 (Elastic IP) | 시간당 $0.005 → **월 약 $3.6**. 인스턴스가 켜져 있든(사용 중) 꺼져 있든(유휴) 같은 요금, 반납해야 멈춤 |
| DuckDNS, Let's Encrypt | 무료 |
| 예시: 평일 하루 4시간 × 22일 | 인스턴스 약 $57 + 디스크·IP 약 $15 = **월 약 $72** |

정확한 값은 [EC2 온디맨드 요금](https://aws.amazon.com/ec2/pricing/on-demand/), [공인 IPv4 요금(VPC 요금 페이지)](https://aws.amazon.com/vpc/pricing/), AWS Pricing Calculator에서 확인하세요.
공인 IPv4 요금은 프리 티어로 면제되지 않을 수 있습니다(VPC 요금 페이지 기준).

### 켜고 끄기 (노트북에서)

```bash
export AWS_REGION=ap-northeast-2
INSTANCE_ID=i-0123456789abcdef0   # 내 인스턴스 ID

aws ec2 start-instances --instance-ids $INSTANCE_ID
aws ec2 wait instance-running --instance-ids $INSTANCE_ID

aws ec2 stop-instances --instance-ids $INSTANCE_ID       # ⚠️ 다 쓰면 꼭
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].State.Name' --output text   # stopped 확인
```

컨테이너는 `restart: unless-stopped`라서 인스턴스를 다시 켜면 자동으로 올라옵니다.

### 예산 알림 (AWS Budgets)

월 예산을 정하고 80%, 100%가 되면 메일을 받습니다(콘솔: Billing → Budgets → 예산 생성 → 비용 예산).

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cat > budget.json <<'JSON'
{"BudgetName": "temi-monthly", "BudgetLimit": {"Amount": "80", "Unit": "USD"},
 "TimeUnit": "MONTHLY", "BudgetType": "COST"}
JSON
cat > notifications.json <<'JSON'
[{"Notification": {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 80, "ThresholdType": "PERCENTAGE"},
  "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "팀메일@example.com"}]},
 {"Notification": {"NotificationType": "FORECASTED", "ComparisonOperator": "GREATER_THAN", "Threshold": 100, "ThresholdType": "PERCENTAGE"},
  "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "팀메일@example.com"}]}]
JSON
aws budgets create-budget --account-id $ACCOUNT_ID --budget file://budget.json \
  --notifications-with-subscribers file://notifications.json
```

### 유휴 시 자동 중지 (깜빡 잊었을 때 대비)

CPU 사용률이 1시간 동안 5% 미만이면 인스턴스를 자동으로 중지합니다.

```bash
aws cloudwatch put-metric-alarm --region ap-northeast-2 --alarm-name temi-idle-stop \
  --namespace AWS/EC2 --metric-name CPUUtilization --dimensions Name=InstanceId,Value=$INSTANCE_ID \
  --statistic Average --period 300 --evaluation-periods 12 --threshold 5 --comparison-operator LessThanThreshold \
  --alarm-actions arn:aws:automate:ap-northeast-2:ec2:stop
```

시연 중 오래 대기하면 꺼질 수 있으니 시연 날에는 기준을 늘리거나 알람을 잠시 끄세요(`aws cloudwatch disable-alarm-actions --alarm-names temi-idle-stop`).

## 7. 처음 띄운 뒤 할 일

1. **모델 확인**: `ollama-init` 로그에서 모델이 받아졌는지, GPU에 올라갔는지 확인합니다.
   ```bash
   docker compose -f docker/compose.ec2.yml exec ollama ollama list
   docker compose -f docker/compose.ec2.yml exec ollama ollama ps      # 워밍업 후 qwen3:14b가 GPU 100%로 보여야 함
   docker compose -f docker/compose.ec2.yml exec ollama nvidia-smi
   ```
2. **평가 실행**: 문항 43개 × 3회입니다. 모델마다 측정 전에 워밍업 요청 1번을 보내고, 그 시간은 결과 파일의 '워밍업' 표에 따로 적습니다(응답 시간 집계에서 제외).
   ```bash
   docker compose -f docker/compose.ec2.yml exec server python scripts/eval_models.py --models qwen3:14b
   # 일부만: ... eval_models.py --models qwen3:14b --only T09,T16,C02,C19 --repeats 1
   # 후보 비교: OLLAMA_PULL_MODELS로 후보를 받은 뒤 --models 생략(.env의 EVAL_MODELS 사용)
   ```
3. **결과 확인**: `~/Server/results/`에 파일 세 개가 생깁니다.
   - `eval_models_{날짜_시각}.md`: 실행 설정·코드 커밋·워밍업, 모델별 비교표, 문항별 성공 횟수, 틀린 문항
   - `eval_runs_{날짜_시각}.csv`: 전체 실행 기록
   - `eval_child_replies_{날짜_시각}.csv`: 아이 모드 답변을 나란히 모은 표 (팀 5점 채점용)
   ```bash
   scp -i <키>.pem 'ubuntu@<Elastic IP>:~/Server/results/*' ./results/   # 노트북으로 가져오기
   ```
4. 모델을 바꾸려면 `.env`의 `TEACHER_LLM__MODEL`, `CHILD_LLM__MODEL`을 고친 뒤 `docker compose ... up -d`로 다시 만듭니다.
5. **다 쓰면 인스턴스를 중지합니다.**
