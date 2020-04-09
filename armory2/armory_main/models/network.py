from django.db import models
from picklefield.fields import PickledObjectField
from .base_model import BaseModel
import pdb
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from armory2.armory_main.included.utilities.color_display import display, display_warning, display_new, display_error
from armory2.armory_main.included.utilities.network_tools import validate_ip, get_ips, private_subnets
from netaddr import IPNetwork, IPAddress
from ipwhois import IPWhois

class BaseDomain(BaseModel):
    name = models.CharField(max_length=64)
    dns = PickledObjectField(default=dict)

    def __str__(self):
        return self.name

class CIDR(BaseModel):

    name = models.CharField(max_length=44, unique=True)
    org_name = models.CharField(max_length=256, unique=False)

    def __str__(self):
        return "{}: {}".format(self.name, self.org_name)
        
class Domain(BaseModel):
    name = models.CharField(max_length=128, unique=True)
    ip_addresses = models.ManyToManyField('IPAddress')
    basedomain = models.ForeignKey(BaseDomain, on_delete=models.CASCADE)
    whois = models.TextField()

    def __str__(self):
        return self.name
        

class IPAddress(BaseModel):
    ip_address = models.CharField(max_length=39, unique=True)
    cidr = models.ForeignKey(CIDR, on_delete=models.CASCADE)
    os = models.CharField(max_length=512)
    whois = models.TextField()
    version = models.IntegerField()
    
    def __str__(self):
        return self.ip_address
        
class Port(BaseModel):

    port_number = models.IntegerField(unique=False)
    proto = models.CharField(max_length=32)
    status = models.CharField(max_length=32, default="open")
    service_name = models.CharField(max_length=256)
    ip_address = models.ForeignKey(IPAddress, on_delete=models.CASCADE)
    
    certs = models.TextField()
    info = PickledObjectField(default=dict)

    def __str__(self):
        return "{} / {} / {}".format(self.proto, self.port_number, self.service_name)
        
# pre_save.connect(Domain.pre_save, sender=Domain)

@receiver(pre_save, sender=BaseDomain)
def pre_save_basedomain(sender, instance, *args, **kwargs):
    if not instance.id:
        display_new("New base domain added: {}  Active Scope: {}    Passive Scope: {}".format(instance.name, instance.active_scope, instance.passive_scope))

@receiver(pre_save, sender=Domain)
def pre_save_domain(sender, instance, *args, **kwargs):
    if not instance.id:
        domain_name = ''.join([i for i in instance.name.lower() if i in 'abcdefghijklmnopqrstuvwxyz.-0123456789'])
        if domain_name.count('.') > 0:
            base_domain = '.'.join(domain_name.split('.')[-2:])

            bd, created = BaseDomain.objects.get_or_create(name=base_domain, defaults="active_scope":instance.active_scope, "passive_scope":instance.passive_scope)

            if not created:
                instance.passive_scope = bd.passive_scope
                instance.active_scope = bd.active_scope


            instance.basedomain = bd

            
            display_new("New domain added: {}  Active Scope: {}    Passive Scope: {}".format(instance.name, instance.active_scope, instance.passive_scope))
        else:
            raise Exception("Invalid domain name provided: {}".format(domain_name))


@receiver(post_save, sender=Domain)
def post_save_domain(sender, instance, created, *args, **kwargs):
    if created:
        domain_name = instance.name
        ips = get_ips(domain_name)

        for i in ips:
            ip, created = IPAddress.objects.get_or_create(ip_address=i)

            
            if ip.active_scope or instance.active_scope:
                instance.active_scope = True
                ip.active_scope = True
                                        
            if instance.passive_scope or ip.passive_scope:
                instance.passive_scope = True
                ip.passive_scope = True

            display_new("IP and Domain {}/{} scope updated to:  Active Scope: {}     Passive Scope: {}".format(i, domain_name, ip.active_scope, ip.passive_scope))
            ip.save()
            instance.ip_addresses.add(ip)
            instance.save()


@receiver(pre_save, sender=IPAddress)
def pre_save_ip(sender, instance, *args, **kwargs):
    if not instance.id:
        res = validate_ip(instance.ip_address)
        if res == "ipv4":
            instance.version = 4
        elif res == "ipv6":
            instance.version = 6
        else:
            raise Exception("Not a valid IPv4 or IPv6 address.")


        # addr = IPAddress(instance.ip_address)

        cidrs = CIDR.objects.all()

        for c in cidrs:
            if instance.ip_address in IPNetwork(c.name):
                instance.active_scope = c.active_scope
                instance.passive_scope = c.passive_scope
                instance.cidr = c
                break
        
        try:
            cidr = instance.cidr
        except CIDR.DoesNotExist:
            cidr_data, org_name = get_cidr_info(instance.ip_address)
            cidr, created = CIDR.objects.get_or_create(name=cidr_data, defaults={'org_name':'org_name'})
            instance.cidr = cidr
        display_new("New IP added: {}  Active Scope: {}    Passive Scope: {}".format(instance.ip_address, instance.active_scope, instance.passive_scope))
        

@receiver(pre_save, sender=CIDR)
def pre_save_cidr(sender, instance, *args, **kwargs):
    if not instance.id and not instance.org_name:
        cidr_data, org_name = get_cidr_info(instance.name.split('/')[0])

        instance.org_name = org_name
    
    if not instance.id:
        display_new("New CIDR added: {} - {} Active Scope: {}    Passive Scope: {}".format(instance.name, instance.org_name, instance.active_scope, instance.passive_scope))

def get_cidr_info(ip_address):
    
    for p in private_subnets:
        if ip_address in p:
            return str(p), 'Non-Public Subnet'


    
    
    try:
        res = IPWhois(ip_address).lookup_whois(get_referral=True)
    except Exception:
        try:
            res = IPWhois(ip_address).lookup_whois()
        except Exception as e:
            display_error("Error trying to resolve whois: {}".format(e))
            res = {}
    if not res.get('nets', []):
        
        display_warning(
            "The networks didn't populate from whois. Defaulting to a /24."
        )
        # again = raw_input("Would you like to try again? [Y/n]").lower()
        # if again == 'y':
        #     time.sleep(5)
        # else:
    
        return '{}.0/24'.format('.'.join(ip_str.split('.')[:3])), "Whois failed to resolve."
        
    cidr_data = []

    for net in res['nets']:
        for cd in net['cidr'].split(', '):
            
            cidr_data.append([len(IPNetwork(cd)), cd, net['description']])

    cidr_data.sort()
    return  cidr_data[0][1], cidr_data[0][2]
    
    