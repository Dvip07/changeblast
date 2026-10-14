#!/usr/bin/env bash
# Create (or reuse) the AWS resources for ChangeBlast. Safe to re-run: each step checks first.
#   IAM role + instance profile (SSM Session Manager + read /changeblast/* parameters)
#   Security group: 80/443 only (no SSH; manage the host with SSM Session Manager)
#   EC2 t3.large, Amazon Linux 2023, 40 GB gp3, IMDSv2 required
#   Elastic IP (point the DNS A record for changeblast.dvip.dev here)
set -euo pipefail
REGION="${AWS_REGION:-ca-central-1}"
NAME=changeblast
TYPE="${INSTANCE_TYPE:-t3.large}"
cd "$(dirname "$0")"
aws() { command aws --region "$REGION" "$@"; }
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# ---- IAM role and instance profile
if ! aws iam get-role --role-name "$NAME-ec2" >/dev/null 2>&1; then
  aws iam create-role --role-name "$NAME-ec2" --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$NAME-ec2" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam put-role-policy --role-name "$NAME-ec2" --policy-name read-changeblast-params --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{\"Effect\": \"Allow\", \"Action\": [\"ssm:GetParametersByPath\", \"ssm:GetParameter\"],
                     \"Resource\": \"arn:aws:ssm:$REGION:$ACCOUNT:parameter/changeblast*\"}]}"
  echo "created role $NAME-ec2"
fi
if ! aws iam get-instance-profile --instance-profile-name "$NAME-ec2" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$NAME-ec2" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$NAME-ec2" --role-name "$NAME-ec2"
  echo "created instance profile; waiting for IAM to propagate"; sleep 15
fi

# ---- Security group: web only
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$NAME-web" Name=vpc-id,Values="$VPC" \
     --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ]; then
  SG=$(aws ec2 create-security-group --group-name "$NAME-web" --description "ChangeBlast: HTTP/HTTPS only" \
       --vpc-id "$VPC" --query GroupId --output text)
  for port in 80 443; do
    aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions \
      "IpProtocol=tcp,FromPort=$port,ToPort=$port,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]" >/dev/null
  done
  echo "created security group $SG (80/443)"
fi

# ---- Instance
INSTANCE=$(aws ec2 describe-instances --filters Name=tag:Name,Values="$NAME" \
           Name=instance-state-name,Values=pending,running,stopping,stopped \
           --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$INSTANCE" = "None" ]; then
  AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
        --query Parameter.Value --output text)
  INSTANCE=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" \
    --iam-instance-profile Name="$NAME-ec2" --security-group-ids "$SG" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=40,VolumeType=gp3,Encrypted=true}' \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --user-data file://user-data.sh \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
  echo "launched $INSTANCE ($TYPE); waiting until running"
  aws ec2 wait instance-running --instance-ids "$INSTANCE"
fi

# ---- Elastic IP
ALLOC=$(aws ec2 describe-addresses --filters Name=tag:Name,Values="$NAME" --query 'Addresses[0].AllocationId' --output text)
if [ "$ALLOC" = "None" ]; then
  ALLOC=$(aws ec2 allocate-address --domain vpc --tag-specifications \
          "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" --query AllocationId --output text)
fi
aws ec2 associate-address --instance-id "$INSTANCE" --allocation-id "$ALLOC" >/dev/null
IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC" --query 'Addresses[0].PublicIp' --output text)

echo
echo "instance: $INSTANCE"
echo "elastic IP: $IP"
echo "DNS: add an A record  changeblast  ->  $IP  at your DNS provider (dvip.dev)"
echo "first boot installs Docker and builds images (~5-8 min); log: /var/log/changeblast-deploy.log"
