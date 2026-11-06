#!/usr/bin/env bash
# свой сертификат подписи Докки для сборок на этом Mac: macOS привязывает Универсальный доступ
# и Запись экрана к сертификату, а не к хэшу сборки, и права переживают обновления.
# Ключ лежит в отдельной связке ~/.dokky-signing, пароль от неё - рядом (только владелец),
# чтобы сборка не спрашивала пароль входа. Сертификат действует 10 лет; при замене права выдаются заново.
set -euo pipefail
D="$HOME/.dokky-signing"
KC="$D/dokky-signing.keychain-db"
if [[ -f "$KC" ]]; then
  echo "Уже есть: $KC"
  exit 0
fi
mkdir -p "$D"
chmod 700 "$D"
umask 077
cd "$D"
openssl rand -hex 32 > keychain-pass
P12_PASS="$(openssl rand -hex 16)"
cat > cert.cnf <<'CNF'
[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=Dokky Local Signing
O=goshkow
[ext]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
subjectKeyIdentifier=hash
CNF
openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes -keyout key.pem -out cert.pem -config cert.cnf 2>/dev/null
# -legacy: security не читает p12 с шифрованием OpenSSL 3 по умолчанию
openssl pkcs12 -export -legacy -inkey key.pem -in cert.pem -name "Dokky Local Signing" -out signing.p12 -passout "pass:$P12_PASS"
security create-keychain -p "$(cat keychain-pass)" "$KC"
security set-keychain-settings "$KC"
security unlock-keychain -p "$(cat keychain-pass)" "$KC"
security import signing.p12 -k "$KC" -P "$P12_PASS" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$(cat keychain-pass)" "$KC" >/dev/null
rm -f key.pem signing.p12
echo "Готово: $(openssl x509 -in cert.pem -noout -fingerprint -sha1)"
