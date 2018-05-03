# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.configure(2) do |config|
  config.vm.box = "fedora-28"
  config.vm.box_url = "http://download.eng.brq.redhat.com/pub/fedora/linux/releases/28/Cloud/x86_64/images/Fedora-Cloud-Base-Vagrant-28-1.1.x86_64.vagrant-libvirt.box"
  config.vm.network "forwarded_port", guest: 80, host: 5000
  config.vm.network "private_network", ip: "192.168.33.10"
  config.vm.synced_folder ".", "/vagrant", type: "sshfs"

  config.vm.provider :libvirt do |domain|
    domain.driver = "kvm"
    domain.memory = 2048
    domain.cpus = 2
  end

  config.vm.provision "ansible" do |ansible|
    ansible.compatibility_mode = "2.0"
    ansible.playbook = "aux/vagrant-playbook.yml"
  end

  config.vm.post_up_message = <<-EOF
Provisioning complete.
Koschei frontend should be running at http://localhost:5000/
See also: https://github.com/msimacek/koschei/#development
EOF
end
