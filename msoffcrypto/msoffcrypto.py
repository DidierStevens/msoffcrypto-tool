import sys, hashlib, base64, binascii, functools, os
from struct import pack, unpack

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import olefile
from xml.dom.minidom import parseString

SEGMENT_LENGTH = 4096

def hashCalc(i, algorithm):
    if algorithm == "SHA512":
        return hashlib.sha512(i)
    else:
        return hashlib.sha1(i)

def decrypt(key, keyDataSalt, hashAlgorithm, ifile, ofile):
    obuf = b''
    totalSize = unpack('<I', ifile.read(4))[0]
    sys.stderr.write("totalSize: {}\n".format(totalSize))
    ifile.seek(8)
    for i, ibuf in enumerate(iter(functools.partial(ifile.read, SEGMENT_LENGTH), b'')):
        saltWithBlockKey = keyDataSalt + pack('<I', i)
        iv = hashCalc(saltWithBlockKey, hashAlgorithm).digest()
        iv = iv[:16]
        aes = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = aes.decryptor()
        dec = decryptor.update(ibuf) + decryptor.finalize()
        obuf += dec
    ofile.write(obuf)
    return True

def generate_skey_from_privkey(privkey, encryptedKeyValue):
    privkey = serialization.load_pem_private_key(privkey.read(), password=None, backend=default_backend())
    skey = privkey.decrypt(encryptedKeyValue, padding.PKCS1v15())
    return skey

def generate_skey_from_password(password, saltValue, hashAlgorithm, encryptedKeyValue, spinValue, keyBits):
    block3 = bytearray([0x14, 0x6e, 0x0b, 0xe7, 0xab, 0xac, 0xd0, 0xd6])
    # Initial round sha512(salt + password)
    h = hashCalc(saltValue + password.encode("UTF-16LE"), hashAlgorithm)

    # Iteration of 0 -> spincount-1; hash = sha512(iterator + hash)
    for i in range(0, spinValue, 1):
        h = hashCalc(pack("<I", i) + h.digest(), hashAlgorithm)

    h2 = hashCalc(h.digest() + block3, hashAlgorithm)
    # Needed to truncate skey to bitsize
    skey3 = h2.digest()[:keyBits//8]

    # AES encrypt the encryptedKeyValue with the skey and salt to get secret key
    aes = Cipher(algorithms.AES(skey3), modes.CBC(saltValue), backend=default_backend())
    decryptor = aes.decryptor()
    skey = decryptor.update(encryptedKeyValue) + decryptor.finalize()
    return skey

def parseinfo(ole):
    versionMajor, versionMinor = unpack('<HH', ole.read(4))
    if versionMajor != 4 or versionMinor != 4:
        raise AssertionError("Unsupported EncryptionInfo version")
    ole.seek(8)
    xml = parseString(ole.read())
    keyDataSalt = base64.b64decode(xml.getElementsByTagName('keyData')[0].getAttribute('saltValue'))
    keyDataHashAlgorithm = xml.getElementsByTagName('keyData')[0].getAttribute('hashAlgorithm')
    password_node = xml.getElementsByTagNameNS("http://schemas.microsoft.com/office/2006/keyEncryptor/password", 'encryptedKey')[0]
    spinValue = int(password_node.getAttribute('spinCount'))
    encryptedKeyValue = base64.b64decode(password_node.getAttribute('encryptedKeyValue'))
    passwordSalt = base64.b64decode(password_node.getAttribute('saltValue'))
    passwordHashAlgorithm = password_node.getAttribute('hashAlgorithm')
    passwordKeyBits = int(password_node.getAttribute('keyBits'))
    info = {
        'keyDataSalt': keyDataSalt,
        'keyDataHashAlgorithm': keyDataHashAlgorithm,
        'encryptedKeyValue': encryptedKeyValue,
        'spinValue': spinValue,
        'passwordSalt': passwordSalt,
        'passwordHashAlgorithm': passwordHashAlgorithm,
        'passwordKeyBits': passwordKeyBits,
    }
    return info

class OfficeFile:
    def __init__(self, file):
        ole = olefile.OleFileIO(file)
        self.file = ole
        self.info = parseinfo(ole.openstream('EncryptionInfo'))
        self.secret_key = None
    def load_skey(self, secret_key):
        self.secret_key = secret_key
    def load_password(self, password):
        self.secret_key = generate_skey_from_password(password, self.info['passwordSalt'], self.info['passwordHashAlgorithm'], self.info['encryptedKeyValue'], self.info['spinValue'], self.info['passwordKeyBits'])
    def load_privkey(self, private_key):
        self.secret_key = generate_skey_from_privkey(private_key, self.info['encryptedKeyValue'])
    def decrypt(self, ofile):
        decrypt(self.secret_key, self.info['keyDataSalt'], self.info['keyDataHashAlgorithm'], self.file.openstream('EncryptedPackage'), ofile)

def ifWIN32SetBinary(io):
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.setmode(io.fileno(), os.O_BINARY)

def main():
    import argparse
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-k', dest='secret_key', help='MS-OFFCRYPTO secretKey value (hex)')
    group.add_argument('-p', dest='private_key', type=argparse.FileType('rb'), help='RSA private key file')
    group.add_argument('-P', dest='password', help='Password ASCII')
    parser.add_argument('infile', nargs='?', type=argparse.FileType('rb'))
    parser.add_argument('outfile', nargs='?', type=argparse.FileType('wb'))
    args = parser.parse_args()

    if not olefile.isOleFile(args.infile):
        raise AssertionError("No OLE file")

    file = OfficeFile(args.infile)

    if args.secret_key:
        file.load_skey(binascii.unhexlify(args.secret_key))
    elif args.private_key:
        file.load_privkey(args.private_key)
    elif args.password:
        file.load_password(args.password)

    if args.outfile == None:
        ifWIN32SetBinary(sys.stdout)
        args.outfile = sys.stdout
    file.decrypt(args.outfile)

if __name__ == '__main__':
    main()
