#!/usr/bin/env python3
import sys
import subprocess
from pathlib import Path

TEAM_MAP = {
    '0033': '74ae65ff-96ae-4da5-a43e-692eb6fee38f',
    'Askar City': 'b2deaacd-08ad-4c14-b97b-b4f382d82ea3',
    'Atlas': 'b61a01d3-b5e0-465a-9b1b-f541862bb5c4',
    'Banminary': '0774b1b2-df40-4cf2-915e-5d5a6133a13a',
    'Bean counters': '47803c19-e264-4492-bd62-fbdd58cfd7e6',
    'Besvikomat': 'e9be6a8c-fb2a-4fd2-970e-5f19352b6d48',
    'Big O': 'f3362833-2d7a-4636-9a3c-e4f10fcebdc1',
    'Clankers': '03ab46df-7058-49ec-a9f5-592f86e9a95a',
    'Coreflood': 'ea0d33c8-ca2b-497a-9be0-1837379eab1e',
    'CtrlAltDefeat': '74e43df6-bad7-474b-8e37-0ea44a2c80f1',
    'Deploy Then Understand': '74de0a45-c1d2-4691-9537-7e8312938373',
    'DinooniD': '4691bd56-45c2-460c-a5a5-eb7563c594cb',
    'Drachenlord': 'c587b37c-f1ce-43ae-ab02-4e775d262cdd',
    'Erebus': '9810ba35-66a9-4af3-9a2f-06651aef4109',
    'Focalground': '00191498-aa36-4f5a-aafb-e432e57607e8',
    'Git Glam': 'db4ecf72-b4d4-43dd-b94b-66d29bbdb81d',
    'HTTP 418': '12fdece5-48c9-4913-9533-6d95b73e22ab',
    'Hugging Farce': 'd667ee5b-bb42-4708-9ed4-cd26e32a14a2',
    'I Stone': 'bfbb9a68-b37a-4a61-b0ea-d36369c8f65a',
    'Jacobs Code': '805df4d2-53cb-40d0-b538-af8772b3ae3d',
    'Juusto': '32087804-2dde-4265-acb2-b6ec9039fbee',
    'Jython': '8cf9b751-00d3-484a-b0ed-e3073ae1d46f',
    'Kings College Munich': 'dfa9be96-f6f6-490b-9c79-d22f9b42369d',
    'Kleos': '3771bc13-b92b-47c6-a190-e1aeb9c36fed',
    'Landers': 'b538e523-2250-4c1d-b718-e73b77ebad55',
    'Leviathan': '26286680-d861-4f9e-9073-a6201bd48d3b',
    'Lorem Ipsum': '017419af-711a-40b0-9826-60cc754bd840',
    'Lunds Stallions': 'eceb8455-7cb3-442b-ba40-c6597c16b446',
    'Memtrace': '787dda79-ced2-4496-aa54-3efe7b087ef1',
    'Mimercraft': '7aab74cd-772f-444b-a6c5-0009f416494e',
    'No': '4f58f904-538d-48f8-93a5-fdc7b4c5ef72',
    'O(1)': '7419695b-3b8f-4a50-8cce-e545b086c670',
    'OopsGotYourElo': 'f61d19c1-600e-457b-861b-dbeb6b3d8691',
    'OpenSverige': '379a5d80-9921-4c9e-949b-f9b1dcba16be',
    'OpenSverige - Plan C': '6338766e-14ca-486c-881c-84011c22b3ba',
    'Ordo Asparagus': '915b92bb-c943-4374-8b99-437b97ad1edb',
    'Oresund Overflow': '03623b3a-50c8-49fe-8b6b-0bed1bddd9b7',
    'Orizon': '01a47fc5-5ae0-433c-94a5-f3efe6e48ec0',
    'Ouroboros': 'a5631594-3000-457e-890d-29d547f9de93',
    'Pantheon': '3a7b78b8-5d79-4e55-94a3-7732fcaa4105',
    'Part-timers': 'f71a9567-4a16-43d1-9550-c7f7f9243632',
    'Pivot': '20064efa-c6a9-4c46-acb7-3f7ea9f9b1c9',
    'Powered by SmartFridge': '7fd91e77-812c-44da-bce7-457be94d2548',
    'Powerpuff Girls': '0c1fea85-495c-41e3-bb30-f6665ddd2cd4',
    'SingleCore': '1056f91a-3bfa-4057-8d79-cd1c47e596be',
    'TRRR': '5734f3eb-4098-49f5-bbf1-49531d0ce811',
    'Team 48': '48340ad8-701f-4a40-850d-1f3f3d56d8ca',
    'The Bisons': 'f670dfed-dfee-421b-8c01-a67b8a278ce3',
    'The Flotte Experience': '78b72cb2-22c7-4e17-bd36-2883eb5a8524',
    'Torsko': 'bd5df261-fb0e-476e-920a-96ef88f7d441',
    'Troupe': '43a1e488-543d-4d51-a066-4f9bf039d1fb',
    'Tyvrets': 'ed36dea6-5c1f-442d-aa48-db7e6c82872b',
    'Viktor5776': 'c0722596-b36d-4aab-a4ed-16e9119a336b',
    'Well have a look': 'b1b79905-260a-4ceb-b491-2b87f426d439',
    'arsonist duck': 'c075251a-92b9-4566-8ebe-a49a1d9d3a57',
    'diverge': '5d46712b-cefb-42dc-923e-1717ee5d2281',
    'farming_200s': '25288fdb-f3a0-4ae2-964f-2fd7eb9b11d7',
    'gsxWins': 'ebd8d82a-7365-4ccb-af0b-defea3a1ac4d',
    'jmc': '6634407e-0c49-4330-89e7-e4179bb691bf',
    'kladde chatte tville (och oss)': 'c7571c87-f960-4c88-a13d-14340bb3200f',
    'lingling_40h': '86d0b484-783c-47dc-99d9-6ed9af2794f8',
    'not adgato': 'fb0e7053-f8f3-4cc8-a38f-1856a518c7d2',
    'opensverige - plan B': 'b7cafd9f-265f-4932-b8b2-f5ba80a6a5ba',
    'ph': '5283c84d-69b0-437b-8dc0-dcb8437a5e48',
    'potatis': 'c8629ab8-e497-4af9-92a2-7cae2a99fb52',
    'sporks': '57175ea4-e87f-44ed-a8c4-d5ee8ba31ecd',
    'team lazy': '648d1d5b-5443-4257-a0aa-7048661b612d'
}

def main():
    if len(sys.argv) < 2:
        print("Usage: python queue_scrimmage.py \"Team Name\"")
        print("   or: python queue_scrimmage.py --id <TEAM_ID>")
        sys.exit(1)

    if sys.argv[1] == "--id" and len(sys.argv) == 3:
        team_id = sys.argv[2]
        display_name = team_id
    else:
        team_name = " ".join(sys.argv[1:])
        team_id = TEAM_MAP.get(team_name)
        
        if not team_id:
            print(f"Error: Team '{team_name}' not found in internal dictionary.")
            print("Try running again with exact casing, or use: python queue_scrimmage.py --id <TEAM_ID>")
            sys.exit(1)
        display_name = f"'{team_name}' ({team_id})"

    print(f"Queueing full 15-map scrimmage against {display_name}...")

    fcode_bin = Path(__file__).parent.parent.parent / ".venv" / "bin" / "fcode"
    
    batches = [
        ["antler", "archipelago", "auroraveil", "drakkarfjord", "drumlin"],
        ["fjordgate", "frostgate", "glacierkeep", "icefloe", "midgard"],
        ["nordkap", "ragnarok", "royale", "valkyrie", "yulerune"]
    ]
    
    for i, batch in enumerate(batches, 1):
        cmd = [str(fcode_bin), "match", "unrated", team_id]
        for m in batch:
            cmd.extend(["--map", m])
            
        print(f"Queueing batch {i}/3...")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Error queueing batch {i}:")
            print(res.stderr)
        else:
            print(f"  Success! {res.stdout.strip()}")
            
    print("All 15 maps queued successfully!")

if __name__ == "__main__":
    main()
