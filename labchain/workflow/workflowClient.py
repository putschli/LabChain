import os
import json
import pprint

from labchain.util.Menu import Menu
from labchain.blockchainClient import TransactionWizard, clear_screen
from labchain.util.TransactionFactory import TransactionFactory
from labchain.workflow.taskTransaction import TaskTransaction, WorkflowTransaction

RESOURCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'resources'))

class TaskTransactionWizard(TransactionWizard):
    """CLI wizard for creating new task transactions and showing workflow status."""

    def __init__(self, wallet, crypto_helper, network_interface):
        super().__init__(wallet, crypto_helper, network_interface)
        self.my_dir = RESOURCE_DIR

    @staticmethod
    def validate_wf_id_input(chosen_wf_id, wf_ids):
        return True if chosen_wf_id in wf_ids else False

    @staticmethod
    def ask_for_task_id(tasks):
        if len(tasks) == 0:
            print(u'There is no task received.')
            input('Press any key to go back to the main menu!')
            return ''

        print(u'Current workflows that are waiting with the following ids: ')
        print()
        tasks = sorted(tasks, key=lambda tup: int(tup[0]))
        for counter, key in enumerate(tasks, 1):
            print(u'- Workflow ID: ' + str(key[0]))
            if len(key[1]) > 0:
                print(u' with input:')
                for dict_key, dict_value in key[1].items():
                    if dict_value == "":
                        dict_value = "-"
                    print("\t* " + dict_key + " : " + dict_value)
            print()

        user_input = input('Please choose a workflow id to work on or press enter to return: ')
        return user_input

    @staticmethod
    def ask_for_workflow_id(workflows):
        print(u'Current workflows that are waiting with the following ids: ')
        print()
        tasks = sorted(workflows, key=lambda tup: int(tup[0]))
        for counter, key in enumerate(tasks, 1):
            print(u'- Workflow ID: ' + str(key[0]))
            print()

        user_input = input('Please choose a workflow id to work on or press enter to return: ')
        return user_input

    def ask_for_receiver(self, receiver_list, comment_dict):
        print(u'Possible receivers listed: ')
        print()

        for tuple in list(enumerate(receiver_list)):
            print(str(tuple[0]+1) + u': '+ comment_dict[tuple[1].split('_')[0]] +':')
            print('...' + str(tuple[1].split('_')[0][84:199]) + '...')
            print()

        user_input = input('Please choose a receiver account (by number) or press enter to return: ')
        return str(int(user_input))

    def ask_for_data(self, modifiable):
        print(u'Please enter values for:')
        data = dict()
        for data_key in modifiable:
            print()
            data[data_key] = input(str(data_key) + u':\t')
        return data

    def validate_receiver_input(self, usr_input, receivers_len):
        try:
            int_usr_input = int(usr_input)
            if int_usr_input <= receivers_len:
                return True
        except ValueError:
            return False

    def check_tasks(self, public_key):
        #   get all the transactions received and sent from the chosen public key
        received = self.network_interface.search_transaction_from_receiver(public_key)
        send = self.network_interface.search_transaction_from_sender(public_key)

        #   separate received transactions into workflow and task transactions
        received_workflow_transaction = [TaskTransaction.from_json(t.get_json_with_signature()) for t in received if
                                     'processes' in t.payload]
        received_task_transaction = [TaskTransaction.from_json(t.get_json_with_signature()) for t in received if
                                     'workflow_id' in t.payload and 'processes' not in t.payload]
        sent_task_transaction = [TaskTransaction.from_json(t.get_json_with_signature()) for t in send if
                                 'workflow_id' in t.payload and 'processes' not in t.payload]

        #   remove or keep the transaction according to split&merge status of the workflow
        sent_task_transaction = self.rearrange_sent_task_transactions(sent_task_transaction)
        received_task_transaction = self.rearrange_received_task_transactions(received_task_transaction)
        received_task_transaction_dict = {self.crypto_helper.hash(t.get_json()): t for t in received_task_transaction}

        #   merge the workflow transactions with the rearranged received transactions
        received_tx_dict = {**received_task_transaction_dict, **{self.crypto_helper.hash(t.get_json()): t for t in received_workflow_transaction}}
        sent_task_transaction_dict = {t.previous_transaction: t for t in sent_task_transaction}
        #   look for the difference of the sent and received transactions and remove duplicates
        diff_set = set(received_tx_dict) - set(sent_task_transaction_dict)
        diff_in_charge_list = [received_tx_dict[k].in_charge.split("_")[0] for k in diff_set]
        diff_in_charge_stages = [received_tx_dict[k].in_charge.split("_")[1] for k in diff_set]
        duplicates = list([x for x in diff_in_charge_list if diff_in_charge_list.count(x) > 1])
        if len(duplicates) > 0:
            max_stage = max(diff_in_charge_stages)
            to_remove = list()
            for elem in diff_set:
                if received_tx_dict[elem].in_charge != "_".join([duplicates[0], max_stage]):
                    to_remove.append(elem)
            for elem in to_remove:
                diff_set.remove(elem)

        #remove completed txs
        completed = list()
        for tx_hash in diff_set:
            if "processes" in received_tx_dict[tx_hash].payload:
                workflow_payload = received_tx_dict[tx_hash].payload
            else:
                workflow_hash = received_tx_dict[tx_hash].payload["workflow_transaction"]
                tx = self.network_interface.requestTransaction(workflow_hash)[0]
                workflow_payload = tx.payload
            last_accounts, all_accounts = self.get_last_accounts(workflow_payload)
            result = True
            owner_last_account = False
            for addr in last_accounts:
                if addr.split("_")[0] == public_key:
                    owner_last_account = True
                if not self.check_if_wf_arrived(addr, workflow_payload["workflow_id"]):
                    result = False
            if result and not owner_last_account:
                completed.append(tx_hash)

        for completed_hash in completed:
            diff_set.remove(completed_hash)

        diff = {k: received_tx_dict[k] for k in diff_set}
        return [diff[k] for k in diff]

    @staticmethod
    def grouped_dict_by_wf_tx(tx_list):
        grouped_dict_by_wf_tx = dict()
        for tx in tx_list:
            key = tx.payload["workflow_transaction"]
            if key in grouped_dict_by_wf_tx:
                grouped_dict_by_wf_tx[key].append(tx)
            else:
                grouped_dict_by_wf_tx[key] = list()
                grouped_dict_by_wf_tx[key].append(tx)
        return grouped_dict_by_wf_tx

    def rearrange_received_task_transactions(self, received_task_transaction):
        grouped_dict_by_wf_tx = self.grouped_dict_by_wf_tx(received_task_transaction)
        for workflow_tx in grouped_dict_by_wf_tx:
            #   retrieve the split dictionary of the wf
            split_dict = WorkflowTransaction.from_json(self.network_interface.requestTransaction(workflow_tx)[0]
                                                       .get_json_with_signature()).splits
            #   just a regular linear wf, nothing to rearrange -> continue
            if len(split_dict.keys()) == 0:
                continue

            #   wf has splits
            #   get process dict to be able to check merge conditions
            process_dict = WorkflowTransaction.from_json(self.network_interface.requestTransaction(workflow_tx)[0]
                                                         .get_json_with_signature()).processes
            for merge_type, merge_addr_list in split_dict.items():
                for addr in merge_addr_list:
                    #   get the potential split senders
                    split_addresses = [sender for sender, receiver_list in process_dict.items() if
                                       addr in receiver_list]
                    #   get the real split senders
                    received_split_txs = [tx for tx in grouped_dict_by_wf_tx[workflow_tx] if tx.in_charge == addr]

                    #   if it is 'and' merge and not all senders sent a transaction, do not show it on received tasks
                    if len(received_split_txs) < len(split_addresses) and merge_type == "AND":
                        for tx in received_split_txs:
                            received_task_transaction.remove(tx)
                    #   if it is 'or' merge and there are multiple transactions that arrive, only show one
                    elif len(received_split_txs) <= len(split_addresses) and merge_type == "OR":
                        if len(received_split_txs) > 1:
                            for received_split_tx in received_split_txs[1:]:
                                received_task_transaction.remove(received_split_tx)
                        else:
                            continue
                        pass
                    #   show only one received tx if the merge condition completed
                    elif len(received_split_txs) == len(split_addresses):
                        received_task_transaction.remove(received_split_txs[0])
        return received_task_transaction

    def rearrange_sent_task_transactions(self, sent_task_transaction):
        grouped_dict_by_wf_id = self.grouped_dict_by_wf_tx(sent_task_transaction)

        for workflow_tx in grouped_dict_by_wf_id:
            #   retrieve the split dictionary of the wf
            split_dict = WorkflowTransaction.from_json(self.network_interface.requestTransaction(workflow_tx)[0]
                                           .get_json_with_signature()).splits

            #   wf has splits
            process_dict = WorkflowTransaction.from_json(self.network_interface.requestTransaction(workflow_tx)[0]
                                                         .get_json_with_signature()).processes
            splits = [lists for lists in process_dict.values() if len(lists) > 1]

            #   just a regular linear wf -> continue
            if len(split_dict.keys()) == 0 and not len(splits) > 0:
                continue

            #   get the in charge lists of the sent transactions
            next_in_charge_list = [tx.in_charge for tx in grouped_dict_by_wf_id[workflow_tx]]
            not_completed = list()
            #   check for splits that are not completed
            for split_list in splits:
                for addr in split_list:
                    if addr not in next_in_charge_list:
                        not_completed.append(split_list)

            #   if the split is not completed, remove the ones that are a part of the split
            for split in not_completed:
                for addr in split:
                    if addr in next_in_charge_list:
                        sent_task_transaction.remove([tx for tx in grouped_dict_by_wf_id[workflow_tx] if tx.in_charge == addr][0])
        return sent_task_transaction

    def get_all_received_workflow_transactions(self, public_key):
        received = self.network_interface.search_transaction_from_receiver(public_key)
        received_workflow_transactions = [WorkflowTransaction.from_json(t.get_json_with_signature()).payload for t in received if
                                     'processes' in t.payload]
        return received_workflow_transactions

    def check_if_wf_arrived(self, public_key, wf_id):
        received = self.network_interface.search_transaction_from_receiver(public_key.split("_")[0])
        received_workflow_transactions = [TaskTransaction.from_json(t.get_json_with_signature()).payload["workflow_id"]==wf_id and TaskTransaction.from_json(t.get_json_with_signature()).payload["in_charge"] == public_key for t in received if
                                     'workflow_id' in t.payload]
        return True if True in received_workflow_transactions else False

    def get_workflow_status(self, workflow_payload):
        #   check if the ending points in the workflow received a transaction of the given workflow
        last_accounts, all_accounts = self.get_last_accounts(workflow_payload)
        result = True
        for addr in last_accounts:
            if not self.check_if_wf_arrived(addr, workflow_payload["workflow_id"]):
                result = False
        if result:
            return "Completed", []
        else:
            remaining_accounts = all_accounts - set(last_accounts)
            waiting_accounts = list()
            for account in remaining_accounts:
                tasks = [task.payload["in_charge"] for task in self.check_tasks(account) if task.payload["workflow_id"] == workflow_payload["workflow_id"]]
                if tasks:
                    waiting_accounts = tasks
            return "In progress", waiting_accounts

    def get_document_details(self, workflow_payload):
        #   get the permissions
        permission_dict = workflow_payload["permissions"]
        wf_id = workflow_payload["workflow_id"]
        clear_screen()
        print("Document details with workflow_id: ", wf_id)
        print()
        #   for each document item, look who's permissioned the change it, and search the transactions made from those addresses
        for key, values in permission_dict.items():
            print("*{:<10s}:".format(key))
            for addr in values:
                related_tx_docs = [tx.payload["document"] for tx in self.network_interface.search_transaction_from_sender(addr.split("_")[0]) if tx.payload["workflow_id"] == wf_id]
                if len(related_tx_docs) == 0:
                    print("\t -")
                else:
                    #   do not include the same change in status, only show different statuses
                    status = ""
                    for doc in related_tx_docs:
                        if key in doc and doc[key] != "":
                            if doc[key] not in status:
                                status += doc[key]
                                status += ", "
                    status = status[:-2]
                    if status == "":
                        status = "-"
                    print("\t", status)
            print()

    @staticmethod
    def get_last_accounts(workflow_payload):
        #   get the ending points in a workflow
        all_addresses = set([item.split('_')[0] for sublist in workflow_payload["processes"].values() for item in sublist])
        all_addresses_with_stages = [item for sublist in workflow_payload["processes"].values() for item in sublist]
        last_accounts = [addr for addr in all_addresses_with_stages if addr not in [item for item in workflow_payload["processes"].keys()]]
        return last_accounts, all_addresses

    def get_workflow_name(self, workflow_payload):
        for file in os.listdir(self.my_dir):
            with open(os.path.join(self.my_dir, file)) as f:
                wf_definition = json.load(f)
                document_cond = set(wf_definition["document"].keys())  == set(workflow_payload["document"].keys())
                permissions_key_cond = set(wf_definition["permissions"].keys())  == set(workflow_payload["permissions"].keys())
                permissions_val_cond = [len(value_list) for value_list in wf_definition["permissions"].values()].sort() == \
                                       [len(value_list) for value_list in workflow_payload["permissions"].values()].sort()
                split_key_cond = set(wf_definition["splits"].keys())  == set(workflow_payload["splits"].keys())
                split_val_cond = [len(value_list) for value_list in wf_definition["splits"].values()].sort() == \
                                       [len(value_list) for value_list in workflow_payload["splits"].values()].sort()

                if document_cond  and permissions_key_cond and permissions_val_cond and split_key_cond and split_val_cond:
                    return file
        return "File not found."

    def show_workflow_status(self):
        """Start wizard to show the current status of the workflows"""
        clear_screen()
        wallet_list = self.wallet_to_list()

        if not len(self.wallet) == 0:
            print("Please choose the account to see related workflows!")
            chosen_key = self.ask_for_key_from_wallet(wallet_list)
            if chosen_key == '':
                return

            while not self.validate_sender_input(chosen_key):
                print("Please choose the account to see related workflows!")
                chosen_key = self.ask_for_key_from_wallet(wallet_list)
                if chosen_key == '':
                    return
                clear_screen()
                print('Invalid input! Please choose a correct index!')
                print()

            clear_screen()
            print(u'Account: ' + str(chosen_key))
            public_key = wallet_list[int(chosen_key) - 1][1]

            workflow_transactions = self.get_all_received_workflow_transactions(public_key)
            if len(workflow_transactions) == 0:
                print("You have not started any workflows!")
                input('Press any key to go back to the main menu!')
                return
            for (key, wf_tx) in enumerate(workflow_transactions):
                status, waiting_addresses = self.get_workflow_status(wf_tx)
                print()
                print(str(key+1) + u':  Workflow id: ' + str(wf_tx["workflow_id"] + '\t---->\t' +
                      self.get_workflow_name(wf_tx) + '\t---->\t' + status))
                if len(waiting_addresses) != 0:
                    print("Waiting for the following accounts: ")
                    for item in set(waiting_addresses):
                        comment = [elem[0] for elem in self.wallet_to_list() if elem[1] == item.split("_")[0]][0]
                        print(u'* '+ comment + ' :')
                        print(' ..' + item[84:199] + '..._' + item.split("_")[1])
                print()
                print("------------------------------------------------------------------")
            input('Press any key to go back to the main menu!')

    def show_workflow_details(self):
        """Start wizard to show the current status of the workflows"""
        clear_screen()
        wallet_list = self.wallet_to_list()

        #   ask for a sender account
        if not len(self.wallet) == 0:
            print("Please choose the account to see related workflows!")
            chosen_key = self.ask_for_key_from_wallet(wallet_list)
            if chosen_key == '':
                return

            while not self.validate_sender_input(chosen_key):
                print("Please choose the account to see related workflows!")
                chosen_key = self.ask_for_key_from_wallet(wallet_list)
                if chosen_key == '':
                    return
                clear_screen()
                print('Invalid input! Please choose a correct index!')
                print()

            clear_screen()
            print(u'Account: ' + str(chosen_key))
            public_key = wallet_list[int(chosen_key) - 1][1]

            workflow_transactions = self.get_all_received_workflow_transactions(public_key)
            if len(workflow_transactions) == 0:
                print("You have not started any workflows!")
                input('Press any key to go back to the main menu!')
                return

            workflow_ids = [wf_tx["workflow_id"] for wf_tx in workflow_transactions]
            chosen_wf_id = self.ask_for_workflow_id(workflow_ids)
            if chosen_wf_id == '':
                return

            # ask for valid wf id input in a loop
            while not self.validate_wf_id_input(chosen_wf_id, [i[0] for i in workflow_ids]):
                clear_screen()
                print('Invalid input! Please choose a correct workflow id!')
                print()
                chosen_wf_id = self.ask_for_workflow_id(workflow_ids)
                if chosen_wf_id == '':
                    return

            wf_tx = [wf_tx for wf_tx in workflow_transactions if wf_tx["workflow_id"] == chosen_wf_id][0]
            self.get_document_details(wf_tx)
            input('Press any key to go back to the main menu!')

    def show(self):
        """Start the wizard."""
        clear_screen()

        # convert dict to an ordered list
        # this needs to be done to get an ordered list that does not change
        # at runtime of the function
        wallet_list = self.wallet_to_list()

        # check if wallet contains any keys
        # case: wallet not empty
        if not len(self.wallet) == 0:

            chosen_key = self.ask_for_key_from_wallet(wallet_list)
            if chosen_key == '':
                return

            # ask for valid sender input in a loop
            while not self.validate_sender_input(chosen_key):
                chosen_key = self.ask_for_key_from_wallet(wallet_list)
                if chosen_key == '':
                    return
                clear_screen()
                print('Invalid input! Please choose a correct index!')
                print()

            clear_screen()
            print(u'Sender: ' + str(chosen_key))


            private_key = wallet_list[int(chosen_key) - 1][2]
            public_key = wallet_list[int(chosen_key) - 1][1]

            #   retrieve waiting tasks
            tasks = self.check_tasks(public_key)
            workflow_ids = [(task.payload['workflow_id'], task.payload['document']) for task in tasks]
            chosen_wf_id = self.ask_for_task_id(workflow_ids)
            if chosen_wf_id == '':
                return

            # ask for valid wf id input in a loop
            while not self.validate_wf_id_input(chosen_wf_id, [i[0] for i in workflow_ids]):
                clear_screen()
                print('Invalid input! Please choose a correct workflow id!')
                print()
                chosen_wf_id = self.ask_for_task_id(workflow_ids)
                if chosen_wf_id == '':
                    return

            clear_screen()
            print(u'Sender: ' + str(chosen_key))
            print(u'Chosen workflow id: ' + str(chosen_wf_id))

            #   arrange workflow_transaction and previous_transaction values
            task = [element for element in tasks if element.payload['workflow_id'] == chosen_wf_id][0]
            task_hash = self.crypto_helper.hash(task.get_json())
            prev_transaction = self.network_interface.requestTransaction(task_hash)[0]
            if type(task) == WorkflowTransaction:
                workflow_transaction_hash = task_hash
                workflow_transaction = prev_transaction
            else:
                workflow_transaction_hash = prev_transaction.payload["workflow_transaction"]
                workflow_transaction = self.network_interface.requestTransaction(workflow_transaction_hash)[0]

            #   check if it is end of the workflow
            in_charge = prev_transaction.payload['in_charge']
            if in_charge in workflow_transaction.payload['processes'].keys():
                next_in_charge_list = workflow_transaction.payload['processes'][in_charge]
            else:
                input("End of workflow. Please press any key to return!")
                return

            # ask for valid receiver input in a loop
            receiver_index = self.ask_for_receiver(next_in_charge_list, workflow_transaction.payload['comments'])
            if receiver_index == '':
                return
            while not self.validate_receiver_input(receiver_index, len(next_in_charge_list)):
                clear_screen()
                print('Invalid input! Please choose a correct receiver!')
                print(u'Sender: ' + str(chosen_key))
                print(u'Chosen workflow id: ' + str(chosen_wf_id))
                receiver_index = self.ask_for_receiver(next_in_charge_list, workflow_transaction.payload['comments'])
                if receiver_index == '':
                    return
                print()
            next_in_charge = next_in_charge_list[int(receiver_index)-1]
            chosen_receiver = next_in_charge.split('_')[0]

            clear_screen()
            print(u'Sender: ' + str(chosen_key))
            print(u'Chosen workflow id: ' + str(chosen_wf_id))
            print(u'Receiver: ' + str(chosen_receiver))

            #   get the document values that the sender is allowed to modify
            attributes = workflow_transaction.payload['permissions'].keys()
            modifiable = list()
            for attr in attributes:
                allowed_list = workflow_transaction.payload['permissions'][attr]
                for allowed in allowed_list:
                    if allowed == in_charge:
                        modifiable.append(attr)

            #   case: sender not allowed to modify anything
            if len(modifiable) == 0:
                print(u'You are not permissioned to modify any data.')
                result = input('Press (y) to send the transaction and (n) to return to main menu!')
                while str(result) != "y" and str(result) != "n":
                    clear_screen()
                    print('You pressed a wrong key.')
                    print(u'Sender: ' + str(chosen_key))
                    print(u'Chosen workflow id: ' + str(chosen_wf_id))
                    print(u'Receiver: ' + str(chosen_receiver))
                    print(u'You are not permissioned to modify any data.')
                    result = input('Press (y) to send the transaction and (n) to return to main menu!')
                if str(result) == "n":
                    return

                data = dict()
            else:
                #   case: ask for the data
                data = self.ask_for_data(modifiable)

            document = data

            #   form the payload and send the transaction
            chosen_payload = dict(workflow_id=chosen_wf_id, document=document,
                                  in_charge=next_in_charge, workflow_transaction=workflow_transaction_hash,
                                  previous_transaction=task_hash)
            new_transaction = TransactionFactory.create_transaction(dict(sender=public_key,
                                                                     receiver=chosen_receiver,
                                                                     payload=chosen_payload,
                                                                     signature=''))
            new_transaction.sign_transaction(self.crypto_helper, private_key)
            transaction_hash = self.crypto_helper.hash(new_transaction.get_json())

            self.network_interface.sendTransaction(new_transaction)
            clear_screen()
            print('Transaction successfully created!')
            print()
            print(u'Sender: ' + wallet_list[int(chosen_key) - 1][2])
            print(u'Receiver: ' + str(chosen_receiver))
            print(u'Payload: ' + str(chosen_payload))
            print(u'Hash: ' + str(transaction_hash))
            print()

        # case: wallet is empty
        else:
            print(u'Wallet does not contain any keys! Please create one first!')

        input('Press any key to go back to the main menu!')

class WorkflowTransactionWizard(TransactionWizard):
    """CLI wizard for creating new workflow transactions."""

    def __init__(self, wallet, crypto_helper, network_interface):
        super().__init__(wallet, crypto_helper, network_interface)
        self.my_dir = RESOURCE_DIR
        self.pp = pprint.PrettyPrinter(indent=2)

    def get_workflow_list(self):
        return [file for file in os.listdir(self.my_dir) if file.endswith(".json")]

    @staticmethod
    def validate_workflow_input(usr_input, workflow_resource_len):
        try:
            int_usr_input = int(usr_input)
        except ValueError:
            return False

        return True if int_usr_input != 0 and int_usr_input <= workflow_resource_len else False

    @staticmethod
    def ask_for_workflow_id():
        return str(input('Please type in a workflow id or press enter to return: '))

    @staticmethod
    def ask_for_key_from_wallet(wallet_list):
        print(u'Current keys in the wallet: ')
        print()
        for counter, key in enumerate(wallet_list, 1):
            print(str(counter) + u':\t' + str(key[0]))
            print(u'\tPublic Key: ...' + str(key[1])[84: 199] + '...')
            print()

        user_input = input('Please choose an account (by number) or press enter to return: ')
        return user_input

    @staticmethod
    def ask_for_workflow(workflow_list):
        print(u'Current workflow files in resources: ')
        for counter, key in enumerate(workflow_list, 1):
            print()
            print(u'\t' + str(counter) + ': ' + str(key))
            print()

        user_input = input('Please choose a workflow file (by number) or press enter to return: ')
        return user_input

    @staticmethod
    def get_all_entities_in_wf(workflow_template):
        task_entities = set()
        in_charge_entity = workflow_template["in_charge"].split("_")[0]
        for key, values in workflow_template["processes"].items():
            if in_charge_entity != key.split("_")[0]:
                task_entities.add(key.split("_")[0])
            for value in values:
                if in_charge_entity != value.split("_")[0]:
                    task_entities.add(value.split("_")[0])
        return in_charge_entity, task_entities

    @staticmethod
    def exchange_entities_with_pks(workflow_template, exchange_dict, wallet_list):
        #   exchange the meaningful names with public key values chosen by the client
        workflow_str = json.dumps(workflow_template)
        for key, value in exchange_dict.items():
            workflow_str = workflow_str.replace(key, value)
        new_workflow = json.loads(workflow_str)
        new_workflow["comments"] = dict()
        for account in wallet_list:
            if account[1] in exchange_dict.values():
                new_workflow["comments"][account[1]] = account[0]
        return new_workflow

    def show(self):
        """Start the wizard."""
        clear_screen()

        wallet_list = self.wallet_to_list()
        workflow_list = self.get_workflow_list()

        if len(workflow_list) == 0:
            #   case: workflow resources are empty
            print(u'There is no workflow file in resources! Please create one first!')
        elif len(self.wallet) == 0:
            #   case: wallet is empty
            print(u'Wallet does not contain any keys! Please create one first!')
        else:
            # ask for valid workflow input in a loop
            chosen_workflow = self.ask_for_workflow(workflow_list)
            if chosen_workflow == '':
                return
            while not self.validate_workflow_input(chosen_workflow, len(workflow_list)):
                chosen_workflow = self.ask_for_workflow(workflow_list)
                if chosen_workflow == '':
                    return
                clear_screen()
                print('Invalid input! Please choose a correct index!')
                print()

            clear_screen()

            path = os.path.join(self.my_dir, str(workflow_list[int(chosen_workflow) - 1]))
            with open(path) as f:
                chosen_payload = json.load(f)

            in_charge_entity, task_entities = self.get_all_entities_in_wf(chosen_payload)
            if len(task_entities) + 1 > len(self.wallet):
                print(u'There is not enough keys in the wallet for the chosen workflow. Please make sure you do!')
                input("Press any key to go back to main menu!")
                return

            print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
            print("Please enter a sender account.")
            chosen_sender = self.ask_for_key_from_wallet(wallet_list)
            if chosen_sender == '':
                return

            # ask for valid sender input in a loop
            while not self.validate_sender_input(chosen_sender):
                clear_screen()
                print('Invalid input! Please choose a correct index!')
                print()
                print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
                print("Please enter a sender account.")
                chosen_key = self.ask_for_key_from_wallet(wallet_list)
                if chosen_key == '':
                    return

            clear_screen()
            sender_private_key = wallet_list[int(chosen_sender) - 1][2]
            sender_public_key = wallet_list[int(chosen_sender) - 1][1]

            exchange_dict = dict()
            exchange_dict[in_charge_entity] = sender_public_key
            # ask for valid input in a loop for each entity in the workflow template
            for entity in task_entities:
                print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
                print(u'Sender: ' + str(wallet_list[int(chosen_sender) - 1][0]))
                print("-------------------------------------------------------------")
                print("Please enter an account for: ", entity)
                chosen_key = self.ask_for_key_from_wallet(wallet_list)
                if chosen_key == '':
                    return

                while not self.validate_sender_input(chosen_key):
                    clear_screen()
                    print('Invalid input! Please choose a correct index!')
                    print()
                    print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
                    print(u'Sender: ' + str(wallet_list[int(chosen_sender) - 1][0]))
                    print("-------------------------------------------------------------")
                    print("Please enter an account for: ", entity)
                    chosen_key = self.ask_for_key_from_wallet(wallet_list)
                    if chosen_key == '':
                        return
                if wallet_list[int(chosen_key) - 1][1] in exchange_dict.values():
                    #   if an address selected multiple times, give warning and cancel the workflow
                    clear_screen()
                    print("You have chosen this address for another person. Either choose another person or change your workflow template!")
                    input("Press any key to go back to main menu!")
                    return
                exchange_dict[entity] = wallet_list[int(chosen_key) - 1][1]
                clear_screen()

            chosen_payload = self.exchange_entities_with_pks(chosen_payload, exchange_dict, self.wallet_to_list())
            print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
            print(u'Your workflow data:')
            self.pp.pprint(chosen_payload)

            # ask for valid workflow id input in a loop
            chosen_workflow_id = self.ask_for_workflow_id()
            if chosen_workflow_id == '':
                return

            while not self.validate_receiver_input(chosen_workflow_id):
                clear_screen()
                print('Invalid input! Please choose a workflow id that is greater than 0!')
                print()
                print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
                print(u'Your workflow data:')
                self.pp.pprint(chosen_payload)
                chosen_workflow_id = self.ask_for_workflow_id()
                if chosen_workflow_id == '':
                    return

            chosen_payload["workflow_id"] = chosen_workflow_id

            # prepare the transaction and send it to the sender
            transaction = TransactionFactory.create_transaction(dict(sender=sender_public_key,
                                                                     receiver=sender_public_key,
                                                                     payload=chosen_payload,
                                                                     signature=''))
            transaction.sign_transaction(self.crypto_helper, sender_private_key)
            self.network_interface.sendTransaction(transaction)
            transaction_hash = self.crypto_helper.hash(transaction.get_json())
            clear_screen()
            print('Workflow successfully created!')
            print()
            print(u'Workflow: ' + str(workflow_list[int(chosen_workflow) - 1]))
            print(u'Hash: ' + str(transaction_hash))
            print()
        input('Press any key to go back to the main menu!')

class WorkflowClient:

    def __init__(self, wallet, network_interface, crypto_helper):
        self.network_interface = network_interface
        self.crypto_helper = crypto_helper
        self.wallet = wallet
        self.workflow_transaction_wizard = WorkflowTransactionWizard(self.wallet,
                                               self.crypto_helper,
                                               self.network_interface)
        self.task_transaction_wizard = TaskTransactionWizard(self.wallet,
                                                                     self.crypto_helper,
                                                                     self.network_interface)
        self.main_menu = Menu(['Main menu'], {
            '1': ('Create workflow transaction', self.send_workflow_transaction, []),
            '2': ('Send task transaction', self.send_task_transaction, []),
            '3': ('Show workflow status', self.show_workflow_status, []),
            '4': ('Show workflow details', self.show_workflow_details, []),
        }, 'Please select a value: ', 'Exit Workflow Client')

    def main(self):
        """Entry point for the client console application."""
        self.main_menu.show()

    def send_workflow_transaction(self):
        self.workflow_transaction_wizard.show()

    def send_task_transaction(self):
        self.task_transaction_wizard.show()

    def show_workflow_status(self):
        self.task_transaction_wizard.show_workflow_status()

    def show_workflow_details(self):
        self.task_transaction_wizard.show_workflow_details()
