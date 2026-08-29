# Khirale IzyPower MQTT Gateway

## Rôle

L'add-on relie le broker MQTT local de Home Assistant au VPS Khirale. Il :

- accepte uniquement les numéros de série configurés ;
- décode localement les payloads ENC1 ;
- publie les données lisibles sous `khirale/decoded/<SN>/<famille>` ;
- relaie les familles MQTT autorisées vers le VPS ;
- reçoit les flux descendants destinés au site ;
- gère les commandes locales Home Assistant et leurs accusés de réception ;
- transforme périodiquement en `sensor` les réponses `refresh` initiées depuis HA, sans modifier leur payload ;
- publie son état de connexion au VPS.

## Configuration

| Option | Description |
|---|---|
| `mqtt_host` | Adresse du broker MQTT local, généralement `core-mosquitto` |
| `mqtt_port` | Port du broker local, généralement `1883` |
| `mqtt_username` | Compte MQTT local |
| `mqtt_password` | Mot de passe MQTT local |
| `site_code` | Code technique du site fourni par Khirale |
| `allowed_serials` | Liste des numéros de série autorisés sur ce site |
| `vps_host` | Adresse du broker VPS Khirale |
| `vps_port` | Port MQTT TLS du VPS, généralement `8883` |
| `vps_username` | Compte MQTT VPS propre au site |
| `vps_password` | Mot de passe MQTT VPS propre au site |

Exemple :

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: utilisateur_local
mqtt_password: mot_de_passe_local
site_code: site01
allowed_serials:
  - "4564156AZEAZ"
vps_host: mqtt.khirale.fr
vps_port: 8883
vps_username: ha_site01
vps_password: mot_de_passe_vps
```

## Topics locaux

- données lisibles : `khirale/decoded/<SN>/<famille>` ;
- commandes Home Assistant : `khirale/control/<SN>/<commande>` ;
- exemple de refresh : `khirale/control/<SN>/refresh` ;
- exemple de redémarrage : `khirale/control/<SN>/reboot`.

## Démarrage

1. Installer et configurer Mosquitto Broker dans Home Assistant.
2. Renseigner toutes les options de l'add-on.
3. Démarrer l'add-on.
4. Vérifier les journaux : les connexions au broker local et au VPS doivent être acceptées.
5. Vérifier la réception d'un topic `khirale/decoded/<SN>/connect` ou `sensor`.

Ne jamais activer l'option MQTT `retain` pour une commande.
